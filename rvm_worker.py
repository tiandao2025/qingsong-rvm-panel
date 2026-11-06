# -*- coding: utf-8 -*-
"""
青松口播抠像 · 后台工作进程
=========================================
由 rvm_panel.py 通过 subprocess 调用。读取 JSON 配置，逐帧跑 RVM（Robust Video Matting）
并向前端输出进度。

stdout 通信协议（逐行）：
  @@LOG <文本>
  @@PROGRESS <0-100>
  @@DONE <输出目录>
  @@ERROR <错误摘要>
"""

import argparse
import json
import os
import sys
import time
import traceback


def _reconfigure():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def emit(kind, msg=""):
    print("@@%s %s" % (kind, msg), flush=True)


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------
def build_model(variant, device, repo_dir=""):
    import torch

    if repo_dir and os.path.isdir(repo_dir):
        emit("LOG", "从本地仓库加载：%s" % repo_dir)
        if repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        from model import MattingNetwork

        net = MattingNetwork(variant=variant)
        ckpt = os.path.join(repo_dir, "rvm_%s.pth" % variant)
        if not os.path.exists(ckpt):
            ckpt = os.path.join(repo_dir, "checkpoint", "rvm_%s.pth" % variant)
        if os.path.exists(ckpt):
            try:
                state = torch.load(ckpt, map_location="cpu")
            except Exception:
                state = torch.load(ckpt, map_location="cpu", weights_only=False)
            net.load_state_dict(state)
            emit("LOG", "权重已载入：%s" % ckpt)
        else:
            emit("LOG", "未找到本地权重，改用 torch.hub 自动下载")
            net = torch.hub.load("PeterL1n/RobustVideoMatting", variant, trust_repo=True)
        return net.eval().to(device)

    emit("LOG", "首次加载会从 GitHub 拉取仓库与权重，请耐心等待...")
    net = torch.hub.load("PeterL1n/RobustVideoMatting", variant, trust_repo=True)
    return net.eval().to(device)


# ---------------------------------------------------------------------------
# 视频写出
# ---------------------------------------------------------------------------
class VideoSink(object):
    """把 [T,3,H,W] 的 0~1 张量逐帧编码成 mp4 / png 序列。"""

    def __init__(self, path, fps, mbps=8, gray=False, png=False, size=None):
        self.png = png
        self.path = path
        self.container = None
        self.stream = None
        self.index = 0
        self.size = size  # (width, height)，None 表示由首帧推断
        if png:
            os.makedirs(path, exist_ok=True)
            from PIL import Image  # noqa: F401

            self.Image = Image
            return

        import av

        self.av = av
        self.container = av.open(path, mode="w")
        try:
            self.stream = self.container.add_stream("libx264", rate=int(round(fps)))
            self.stream.pix_fmt = "gray" if gray else "yuv420p"
            self.stream.options = {
                "crf": "18",
                "preset": "veryfast",
                "bit_rate": str(int(mbps * 1000 * 1000)),
            }
        except Exception:
            self.stream = self.container.add_stream("mpeg4", rate=int(round(fps)))
            self.stream.pix_fmt = "yuv420p"
        self._size_applied = False
        if self.size:
            self._apply_size(self.size[0], self.size[1])
        self.gray = gray

    def _apply_size(self, w, h):
        """显式指定编码尺寸，避免 PyAV 按默认 640x480 自动缩放。"""
        w = max(2, int(w) - int(w) % 2)
        h = max(2, int(h) - int(h) % 2)
        try:
            self.stream.width = w
            self.stream.height = h
        except Exception:
            try:
                self.stream.codec_context.width = w
                self.stream.codec_context.height = h
            except Exception:
                pass
        self._size_applied = True
        self.size = (w, h)

    def write(self, tensor):
        """tensor: [T,3,H,W] float 0~1（gray=True 时可传 [T,1,H,W]）"""
        frames = tensor.clamp(0, 1).mul(255).byte().permute(0, 2, 3, 1).cpu().numpy()
        if not self.png and not self._size_applied and frames.shape[0]:
            self._apply_size(frames.shape[2], frames.shape[1])
        for arr in frames:
            if self.png:
                img = self.Image.fromarray(arr[:, :, 0] if arr.shape[2] == 1 else arr)
                img.save(os.path.join(self.path, "%06d.png" % self.index))
            else:
                if arr.shape[2] == 1:
                    frame = self.av.VideoFrame.from_ndarray(arr[:, :, 0], format="gray")
                else:
                    frame = self.av.VideoFrame.from_ndarray(arr, format="rgb24")
                for packet in self.stream.encode(frame):
                    self.container.mux(packet)
            self.index += 1

    def close(self):
        if self.container is not None:
            try:
                for packet in self.stream.encode():
                    self.container.mux(packet)
            except Exception:
                pass
            self.container.close()


# ---------------------------------------------------------------------------
# 背景合成
# ---------------------------------------------------------------------------
def norm_variant(raw):
    """把界面显示文字（如 resnet50（边缘更细，推荐））归一化成合法模型名。"""
    text = str(raw or "").strip().lower()
    if "mobilenet" in text:
        return "mobilenetv3"
    return "resnet50"


def _fit_cover(img, w, h):
    """等比缩放 + 居中裁剪到目标尺寸（不拉伸变形）。"""
    from PIL import Image

    iw, ih = img.size
    if (iw, ih) == (w, h):
        return img
    scale = max(w / float(iw), h / float(ih))
    nw = max(w, int(round(iw * scale)))
    nh = max(h, int(round(ih * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


class BackgroundProvider(object):
    """按需给出与前景同步的背景帧：图片常驻、视频循环、无则绿幕。"""

    def __init__(self, path, width, height, device):
        self.path = (path or "").strip()
        self.w = max(2, int(width))
        self.h = max(2, int(height))
        self.device = device
        self.kind = "green"
        self._static = None
        self._gen = None
        self.enabled = False
        if not self.path:
            return
        if not os.path.isfile(self.path):
            emit("LOG", "背景文件不存在，回退绿幕：%s" % self.path)
            return
        ext = os.path.splitext(self.path)[1].lower()
        try:
            if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"):
                self.kind = "image"
                self._static = self._load_image(self.path)
            else:
                self.kind = "video"
                self._gen = self._video_iter(self.path)
            self.enabled = True
        except Exception as exc:
            emit("LOG", "背景载入失败（%s），回退绿幕" % exc)
            self.kind = "green"
            self.enabled = False

    def _to_tensor(self, pil_img):
        import numpy as np
        import torch

        arr = np.asarray(pil_img.convert("RGB"), dtype="float32") / 255.0
        return torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(self.device)

    def _load_image(self, p):
        from PIL import Image

        with Image.open(p) as im:
            return self._to_tensor(_fit_cover(im.convert("RGB"), self.w, self.h))

    def _video_iter(self, p):
        import av
        from PIL import Image

        while True:
            container = av.open(p)
            try:
                for frame in container.decode(video=0):
                    im = Image.fromarray(frame.to_ndarray(format="rgb24"))
                    yield _fit_cover(im, self.w, self.h)
            finally:
                container.close()

    def batch(self, n, size=None):
        """返回 [n,3,H,W] 背景张量，绿幕模式返回 None。"""
        import torch

        if not self.enabled:
            return None
        if self.kind == "image":
            out = self._static.expand(n, -1, -1, -1)
        else:
            out = torch.cat([self._to_tensor(next(self._gen)) for _ in range(n)], dim=0)
        if size and tuple(out.shape[-2:]) != tuple(size):
            out = torch.nn.functional.interpolate(out, size=tuple(size),
                                                  mode="bilinear", align_corners=False)
        return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    _reconfigure()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    inp = cfg["input"]
    out_dir = cfg["output_dir"]
    raw_variant = cfg.get("variant", "resnet50")
    variant = norm_variant(raw_variant)
    if variant != str(raw_variant).strip():
        emit("LOG", "模型档位归一化：%s -> %s" % (raw_variant, variant))
    ratio = float(cfg.get("downsample_ratio", 0.25))
    seq_chunk = max(1, int(cfg.get("seq_chunk", 12)))
    want_comp = bool(cfg.get("out_composition", True))
    want_fgr = bool(cfg.get("out_foreground", False))
    want_alpha = bool(cfg.get("out_alpha", False))
    out_type = cfg.get("output_type", "video")
    mbps = int(cfg.get("video_mbps", 8))
    max_sec = float(cfg.get("max_seconds", 0) or 0)
    device_name = cfg.get("device", "cuda")
    repo_dir = (cfg.get("repo_dir", "") or "").strip()
    if not repo_dir:
        local_repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rvm_repo")
        if os.path.isdir(local_repo):
            repo_dir = local_repo
    bg_path = (cfg.get("background", "") or "").strip()

    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(inp))[0]

    t0 = time.time()
    emit("LOG", "输入：%s" % inp)
    emit("LOG", "输出：%s" % out_dir)

    import numpy as np
    import torch

    if device_name == "cuda" and not torch.cuda.is_available():
        emit("LOG", "CUDA 不可用，自动降级到 CPU（会非常慢）")
        device_name = "cpu"
    device = torch.device(device_name)
    if device.type == "cuda":
        emit("LOG", "显卡：%s" % torch.cuda.get_device_name(0))

    model = build_model(variant, device, repo_dir)
    emit("LOG", "模型就绪：%s" % variant)

    import av

    container = av.open(inp)
    vstream = container.streams.video[0]
    vstream.thread_type = "AUTO"
    fps = float(vstream.average_rate) if vstream.average_rate else 30.0
    total = int(vstream.frames or 0)
    duration = 0.0
    if vstream.duration and vstream.time_base:
        duration = float(vstream.duration * vstream.time_base)
    if not total and duration:
        total = int(duration * fps)

    limit_frames = total
    if max_sec > 0:
        limit_frames = int(max_sec * fps)
        if total:
            limit_frames = min(limit_frames, total)
    emit("LOG", "分辨率 %dx%d | %.2f fps | 总帧数 %s | 预计处理 %s 帧" % (
        vstream.codec_context.width, vstream.codec_context.height, fps,
        total or "未知", limit_frames or "全部"))

    is_png = (out_type == "png_sequence")
    out_size = (vstream.codec_context.width, vstream.codec_context.height)
    sinks = []
    if want_comp:
        sinks.append(("comp", VideoSink(
            os.path.join(out_dir, "comp") if is_png else os.path.join(out_dir, "%s_comp.mp4" % stem),
            fps, mbps, gray=False, png=is_png, size=out_size), "composition"))
    if want_fgr:
        sinks.append(("fgr", VideoSink(
            os.path.join(out_dir, "fgr") if is_png else os.path.join(out_dir, "%s_fgr.mp4" % stem),
            fps, mbps, gray=False, png=is_png, size=out_size), "foreground"))
    if want_alpha:
        sinks.append(("pha", VideoSink(
            os.path.join(out_dir, "pha") if is_png else os.path.join(out_dir, "%s_alpha.mp4" % stem),
            fps, mbps, gray=True, png=is_png, size=out_size), "alpha"))

    bg = torch.tensor([0.0, 1.0, 0.0], device=device).view(1, 3, 1, 1)  # 绿幕兜底
    bg_provider = BackgroundProvider(bg_path, out_size[0], out_size[1], device)
    if want_comp:
        if bg_provider.kind == "image":
            emit("LOG", "背景：图片 %s" % os.path.basename(bg_path))
        elif bg_provider.kind == "video":
            emit("LOG", "背景：视频 %s（自动循环铺满）" % os.path.basename(bg_path))
        else:
            emit("LOG", "背景：纯绿幕（未选背景文件）")
    rec = [None] * 4
    buf = []
    done_frames = 0
    last_emit = 0.0

    def flush(block):
        nonlocal rec, done_frames
        src = torch.from_numpy(np.stack(block)).permute(0, 3, 1, 2).float().div(255.0).to(device)
        bs = int(src.shape[0])
        # 尾块 batch 数与前块不同时，循环状态尺寸不匹配，需重置
        if rec[0] is not None and bs != flush.last_bs:
            rec = [None] * 4
        flush.last_bs = bs
        fgr, pha, *rec = model(src, *rec, downsample_ratio=ratio)
        for _, sink, tag in sinks:
            if tag == "composition":
                bg_frames = bg_provider.batch(bs, size=fgr.shape[-2:])
                if bg_frames is None:
                    sink.write(fgr * pha + bg * (1 - pha))
                else:
                    sink.write(fgr * pha + bg_frames * (1 - pha))
            elif tag == "foreground":
                sink.write(fgr)
            else:
                sink.write(pha.repeat(1, 3, 1, 1) if sink.png else pha)
        done_frames += src.shape[0]

    flush.last_bs = 0

    with torch.no_grad():
        for frame in container.decode(video=0):
            buf.append(frame.to_ndarray(format="rgb24"))
            if len(buf) >= seq_chunk:
                flush(buf)
                buf = []
                if limit_frames and done_frames >= limit_frames:
                    break
                now = time.time()
                if now - last_emit > 0.25:
                    last_emit = now
                    base = limit_frames or total
                    pct = (done_frames / base * 100.0) if base else 0.0
                    speed = done_frames / max(now - t0, 1e-6)
                    emit("PROGRESS", "%.1f|%.1f|%d|%d" % (min(pct, 99.9), speed, done_frames, base or 0))
        if buf:
            flush(buf)

    for _, sink, _ in sinks:
        sink.close()
    container.close()

    cost = time.time() - t0
    emit("LOG", "完成 %d 帧，耗时 %.1f 秒（%.1f 帧/秒）" % (
        done_frames, cost, done_frames / max(cost, 1e-6)))
    emit("PROGRESS", "100.0|%.1f|%d|%d" % (
        done_frames / max(cost, 1e-6), done_frames, done_frames))
    emit("DONE", out_dir)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("ERROR", "%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
        sys.exit(1)
