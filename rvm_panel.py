# -*- coding: utf-8 -*-
"""
青松口播抠像面板（RVM 本地抠像 · Windows 桌面版）
=========================================
把 Robust Video Matting 封装成一个可视化面板：选视频、选场景、点开始，等成品。

特点：
  - 纯 Python 标准库界面（tkinter），面板本身零第三方依赖
  - 真正干活由同目录 rvm_worker.py 在装了 torch 的解释器里跑
  - 实时进度 + 滚动日志 + 设置记忆（panel_config.json）
  - 内置环境自检与一键装环境

运行：双击「启动抠像面板.bat」，或 python rvm_panel.py
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "青松口播抠像面板"
APP_VERSION = "v1.0.2"
APP_BRAND = "青松设计"


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """随包内联的资源（assets、rvm_worker.py）目录。

    打包成单文件 exe 后，资源被解压到 PyInstaller 的 _MEIPASS 临时目录。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", base_dir())
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = base_dir()
RES_DIR = resource_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "panel_config.json")
WORKER = os.path.join(RES_DIR, "rvm_worker.py")
DEFAULT_OUT = os.path.join(BASE_DIR, "output")

# 视觉
BG = "#eef4f4"
CARD = "#ffffff"
PRIMARY = "#0f9d8a"
PRIMARY_DK = "#0a7a6b"
TEXT = "#16302c"
MUTED = "#6d8380"
LOG_BG = "#0e1a18"
LOG_FG = "#bfeadf"

PRESETS = [
    ("口播人像（推荐 · 边缘更实）", 0.5),
    ("口播人像（快速预览）", 0.25),
    ("1080P 半身人像", 0.25),
    ("1080P 全身 / 走动", 0.4),
    ("720P 半身人像", 0.375),
    ("720P 全身 / 走动", 0.6),
    ("4K 半身人像", 0.125),
    ("4K 全身", 0.2),
    ("自定义（手动填下采样比）", None),
]

# 模型档位：界面显示文字 <=> 真实模型名，二者必须分离（防止把中文标签传给 torch.hub）
VARIANT_CHOICES = ["resnet50（边缘更细，推荐）", "mobilenetv3（更快）"]
VARIANT_MAP = {
    "resnet50（边缘更细，推荐）": "resnet50",
    "mobilenetv3（更快）": "mobilenetv3",
}

BG_FILETYPES = [
    ("背景文件（图片 / 视频）",
     "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.mp4 *.mov *.mkv *.avi *.flv *.webm *.m4v *.wmv"),
    ("背景图片", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
    ("背景视频", "*.mp4 *.mov *.mkv *.avi *.flv *.webm *.m4v *.wmv"),
    ("所有文件", "*.*"),
]


def find_system_python():
    """找一个可用的系统 Python 解释器。

    打包成 exe 后 sys.executable 指向面板自身，绝不能拿去执行 -m venv / -c，
    所以必须单独探测：.venv → PATH → 常见安装位置 → py 启动器。
    """
    me = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else ""
    cands = []

    venv_py = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        cands.append(venv_py)

    if not getattr(sys, "frozen", False):
        cands.append(sys.executable)

    for name in ("python.exe", "python3.exe"):
        found = shutil.which(name)
        if found:
            cands.append(found)

    local_app = os.environ.get("LOCALAPPDATA", "")
    for ver in ("Python313", "Python312", "Python311", "Python310", "Python39"):
        cands.append(os.path.join(local_app, "Programs", "Python", ver, "python.exe"))
        cands.append("C:\\%s\\python.exe" % ver)

    for cand in cands:
        if not cand or not os.path.exists(cand):
            continue
        if me and os.path.abspath(cand).lower() == me.lower():
            continue
        return cand
    return ""


def pick_python():
    """推理/装环境要用的解释器：优先 .venv，其次系统 Python。"""
    venv_py = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        return venv_py
    if getattr(sys, "frozen", False):
        return find_system_python()
    return sys.executable


def no_window_flags():
    if os.name == "nt":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


def fmt_duration(seconds):
    """把秒数格式化成 12秒 / 3分05秒 / 1小时02分。"""
    try:
        seconds = max(0, int(round(float(seconds))))
    except Exception:
        return "--"
    if seconds < 60:
        return "%d 秒" % seconds
    if seconds < 3600:
        return "%d 分 %02d 秒" % (seconds // 60, seconds % 60)
    return "%d 小时 %02d 分" % (seconds // 3600, (seconds % 3600) // 60)


def load_logo_image(master, size=56, name="logo_header.png"):
    """加载 assets 下的品牌 logo，按整数倍缩放到约 size 像素（tk 原生，不依赖 PIL）。"""
    for fn in (name, "logo_header.png", "logo_badge.png", "logo.png"):
        path = ""
        for root in (os.path.join(BASE_DIR, "assets"), os.path.join(RES_DIR, "assets")):
            cand = os.path.join(root, fn)
            if os.path.exists(cand):
                path = cand
                break
        if not path:
            continue
        try:
            img = tk.PhotoImage(master=master, file=path)
        except Exception:
            continue
        longest = max(img.width(), img.height())
        if longest > size:
            step = max(1, int(round(longest / float(size))))
            try:
                img = img.subsample(step, step)
            except Exception:
                pass
        return img
    return None


class RvmPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s %s · %s" % (APP_BRAND, APP_TITLE, APP_VERSION))
        self.geometry("980x760")
        self.minsize(900, 680)
        self.configure(bg=BG)

        # 窗口 / 任务栏图标：用品牌 logo
        self._icon_img = None
        try:
            self._icon_img = load_logo_image(self, 128, "logo_icon.png")
            if self._icon_img is not None:
                self.iconphoto(True, self._icon_img)
        except Exception:
            self._icon_img = None

        self.msg_q = queue.Queue()
        self.proc = None
        self.worker_thread = None
        self.install_thread = None
        self.env_info = {}

        self.var_input = tk.StringVar()
        self.var_outdir = tk.StringVar(value=DEFAULT_OUT)
        self.var_preset = tk.StringVar(value=PRESETS[0][0])
        self.var_variant = tk.StringVar(value="resnet50")  # 只存合法模型名
        self.var_variant_text = tk.StringVar(value=VARIANT_CHOICES[0])  # 只存界面显示文字
        self.var_bg = tk.StringVar(value="")  # 背景文件（图片或视频），空 = 绿幕
        self.var_ratio = tk.StringVar(value="0.5")
        self.var_chunk = tk.StringVar(value="12")
        self.var_mbps = tk.StringVar(value="8")
        self.var_png = tk.StringVar(value="video")
        self.var_trial = tk.StringVar(value="0")
        self.var_comp = tk.BooleanVar(value=True)
        self.var_fgr = tk.BooleanVar(value=False)
        self.var_pha = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="就绪")
        self.var_env = tk.StringVar(value="环境检测中...")

        self._init_style()
        self._build_ui()
        self._load_config()
        self.after(120, self.detect_env_async)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if "--self-test" in sys.argv:
            self.after(900, self.destroy)

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------
    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        ui_font = ("Microsoft YaHei UI", 9)
        self.option_add("*Font", ui_font)

        style.configure(".", background=BG, foreground=TEXT, font=ui_font)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Bg.TFrame", background=BG)
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Hint.TLabel", background=CARD, foreground=MUTED,
                        font=("Microsoft YaHei UI", 8))
        style.configure("TLabelframe", background=CARD, bordercolor="#d6e4e1")
        style.configure("TLabelframe.Label", background=BG, foreground=PRIMARY_DK,
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#cfdedb")
        style.configure("TCombobox", fieldbackground="#ffffff")
        style.configure("Accent.TButton", background=PRIMARY, foreground="#ffffff",
                        borderwidth=0, focusthickness=0, padding=(16, 8),
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton",
                  background=[("active", PRIMARY_DK), ("disabled", "#a9c9c3")])
        style.configure("TButton", padding=(12, 6))
        style.configure("Bar.Horizontal.TProgressbar", troughcolor="#dbe8e5",
                        background=PRIMARY, bordercolor="#dbe8e5",
                        lightcolor=PRIMARY, darkcolor=PRIMARY, thickness=18)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = ttk.Frame(self, style="Bg.TFrame", padding=(18, 14, 18, 10))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        # 顶栏：左 logo ｜ 中 标题 ｜ 右 品牌与版本
        head = ttk.Frame(root, style="Bg.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        head.columnconfigure(1, weight=1)
        self._logo_img = load_logo_image(self, 56)
        if self._logo_img is not None:
            tk.Label(head, image=self._logo_img, bg=BG, bd=0,
                     highlightthickness=0).grid(row=0, column=0, rowspan=2,
                                                sticky="w", padx=(0, 12))
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(head, text="Robust Video Matting · 本地推理 · 不上传 · 无水印 · 不限时长",
                  style="Sub.TLabel").grid(row=1, column=1, sticky="w", pady=(2, 0))
        ttk.Label(head, text=APP_BRAND + "  " + APP_VERSION, style="Sub.TLabel").grid(
            row=0, column=2, rowspan=2, sticky="e")

        # 素材
        f1 = ttk.LabelFrame(root, text=" 素材 ", padding=(14, 10))
        f1.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        f1.columnconfigure(1, weight=1)
        ttk.Label(f1, text="原视频", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(f1, textvariable=self.var_input).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(f1, text="浏览...", command=self.browse_input).grid(row=0, column=2)
        ttk.Label(f1, text="输出目录", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(f1, textvariable=self.var_outdir).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(f1, text="浏览...", command=self.browse_outdir).grid(row=1, column=2, pady=(8, 0))

        ttk.Label(f1, text="背景素材", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(f1, textvariable=self.var_bg).grid(row=2, column=1, sticky="ew", padx=8, pady=(8, 0))
        bgbtns = ttk.Frame(f1, style="Card.TFrame")
        bgbtns.grid(row=2, column=2, pady=(8, 0), sticky="w")
        ttk.Button(bgbtns, text="选图片/视频", command=self.browse_background).pack(side="left")
        ttk.Button(bgbtns, text="清空", width=6, command=self.clear_background).pack(side="left", padx=(4, 0))
        ttk.Label(f1, text="留空 = 输出纯绿幕；图片为固定背景，视频自动循环铺满（等比不拉伸）",
                  style="Hint.TLabel").grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=(4, 0))

        # 参数
        f2 = ttk.LabelFrame(root, text=" 抠像参数 ", padding=(14, 10))
        f2.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        f2.columnconfigure(1, weight=1)

        ttk.Label(f2, text="场景预设", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(f2, textvariable=self.var_preset, state="readonly",
                          values=[p[0] for p in PRESETS])
        cb.grid(row=0, column=1, sticky="ew", padx=8)
        cb.bind("<<ComboboxSelected>>", self.on_preset)

        ttk.Label(f2, text="模型精度", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(14, 0))
        # 显示文字走 var_variant_text，真实模型名单独存 var_variant
        self.cb_variant = ttk.Combobox(f2, textvariable=self.var_variant_text, state="readonly",
                                       width=22, values=VARIANT_CHOICES)
        self.cb_variant.grid(row=0, column=3, sticky="w", padx=8)
        self.cb_variant.current(0)
        self.var_variant.set(VARIANT_MAP[VARIANT_CHOICES[0]])
        self.cb_variant.bind("<<ComboboxSelected>>", self.on_variant)

        row = ttk.Frame(f2, style="Card.TFrame")
        row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Label(row, text="下采样比", style="Card.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.var_ratio, width=8).pack(side="left", padx=(6, 18))
        ttk.Label(row, text="并行帧数", style="Card.TLabel").pack(side="left")
        ttk.Combobox(row, textvariable=self.var_chunk, width=6, state="readonly",
                     values=["1", "4", "8", "12", "16", "24"]).pack(side="left", padx=(6, 18))
        ttk.Label(row, text="码率", style="Card.TLabel").pack(side="left")
        ttk.Combobox(row, textvariable=self.var_mbps, width=6, state="readonly",
                     values=["4", "8", "12", "16", "24"]).pack(side="left", padx=(6, 8))
        ttk.Label(row, text="Mbps", style="Hint.TLabel").pack(side="left")

        row2 = ttk.Frame(f2, style="Card.TFrame")
        row2.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(row2, text="合成画面", variable=self.var_comp).pack(side="left")
        ttk.Checkbutton(row2, text="前景（无背景）", variable=self.var_fgr).pack(side="left", padx=14)
        ttk.Checkbutton(row2, text="Alpha 通道", variable=self.var_pha).pack(side="left")
        ttk.Label(row2, text="   输出格式", style="Card.TLabel").pack(side="left", padx=(18, 0))
        ttk.Radiobutton(row2, text="视频 MP4", value="video", variable=self.var_png).pack(side="left", padx=6)
        ttk.Radiobutton(row2, text="PNG 序列（抠图合成用）", value="png_sequence",
                        variable=self.var_png).pack(side="left")

        row3 = ttk.Frame(f2, style="Card.TFrame")
        row3.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Label(row3, text="试跑时长", style="Card.TLabel").pack(side="left")
        ttk.Spinbox(row3, from_=0, to=3600, increment=5, width=8,
                    textvariable=self.var_trial).pack(side="left", padx=6)
        ttk.Label(row3, text="秒（0 = 处理整条视频，建议先试跑 10 秒看效果）",
                  style="Hint.TLabel").pack(side="left")

        # 操作
        act = ttk.Frame(root, style="Bg.TFrame")
        act.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        self.btn_start = ttk.Button(act, text="开始抠像", style="Accent.TButton", command=self.start_job)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(act, text="停止", command=self.stop_job, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(act, text="打开输出目录", command=self.open_outdir).pack(side="left")
        ttk.Button(act, text="环境自检", command=self.detect_env_async).pack(side="left", padx=8)
        ttk.Button(act, text="一键装环境", command=self.install_env).pack(side="left")

        # 进度
        prog = ttk.Frame(root, style="Bg.TFrame")
        prog.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        prog.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(prog, style="Bar.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.lbl_pct = ttk.Label(prog, text="0%", style="Sub.TLabel", width=7, anchor="e")
        self.lbl_pct.grid(row=0, column=1, padx=(8, 0))

        # 日志
        logf = ttk.LabelFrame(root, text=" 运行日志 ", padding=(10, 8))
        logf.grid(row=5, column=0, sticky="nsew")
        root.rowconfigure(5, weight=1)
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log = tk.Text(logf, height=12, bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
                           relief="flat", font=("Consolas", 9), wrap="word", padx=10, pady=8)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set, state="disabled")

        # 状态栏
        stat = ttk.Frame(root, style="Bg.TFrame")
        stat.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        stat.columnconfigure(0, weight=1)
        ttk.Label(stat, textvariable=self.var_status, style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(stat, textvariable=self.var_env, style="Sub.TLabel").grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def on_preset(self, _evt=None):
        name = self.var_preset.get()
        for label, ratio in PRESETS:
            if label == name and ratio is not None:
                self.var_ratio.set(str(ratio))

    def on_variant(self, _evt=None):
        val = self.var_variant_text.get()
        self.var_variant.set(VARIANT_MAP.get(val, "resnet50"))
        self._save_config()

    def browse_background(self):
        path = filedialog.askopenfilename(title="选择背景图片或视频", filetypes=BG_FILETYPES)
        if path:
            path = os.path.normpath(path)
            self.var_bg.set(path)
            self.log_line("背景素材：%s" % path, "ok")

    def clear_background(self):
        self.var_bg.set("")
        self.log_line("背景已清空：合成画面输出纯绿幕", "warn")

    def browse_input(self):
        path = filedialog.askopenfilename(
            title="选择要抠像的视频",
            filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.avi *.flv *.wmv *.m4v *.webm"), ("全部文件", "*.*")])
        if path:
            self.var_input.set(path.replace("/", "\\"))
            if not self.var_outdir.get().strip():
                self.var_outdir.set(DEFAULT_OUT)

    def browse_outdir(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.var_outdir.set(path.replace("/", "\\"))

    def open_outdir(self):
        path = self.var_outdir.get().strip() or DEFAULT_OUT
        os.makedirs(path, exist_ok=True)
        os.startfile(path)  # noqa: 仅 Windows

    def log_line(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), text))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy):
        self.btn_start.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    # ------------------------------------------------------------------
    # 环境
    # ------------------------------------------------------------------
    def detect_env_async(self):
        self.var_env.set("环境检测中...")

        def work():
            py = pick_python()
            if not py or not os.path.exists(py):
                self.msg_q.put(("env", {"error": "未找到 Python 解释器"}))
                return
            script = (
                "import json,sys,shutil\n"
                "d={}\n"
                "d['python']=sys.version.split()[0]\n"
                "d['exe']=sys.executable\n"
                "try:\n"
                "    import torch\n"
                "    d['torch']=torch.__version__\n"
                "    d['cuda']=bool(torch.cuda.is_available())\n"
                "    d['gpu']=torch.cuda.get_device_name(0) if d['cuda'] else ''\n"
                "except Exception as e:\n"
                "    d['torch']='缺失'\n"
                "    d['cuda']=False\n"
                "    d['gpu']=''\n"
                "try:\n"
                "    import av; d['av']=av.__version__\n"
                "except Exception: d['av']='缺失'\n"
                "try:\n"
                "    import PIL; d['pillow']=PIL.__version__\n"
                "except Exception: d['pillow']='缺失'\n"
                "d['ffmpeg']=shutil.which('ffmpeg') or ''\n"
                "print('@@'+json.dumps(d, ensure_ascii=False))\n"
            )
            try:
                out = subprocess.run([py, "-c", script], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace",
                                     creationflags=no_window_flags(), timeout=90)
                payload = ""
                for line in (out.stdout or "").splitlines():
                    if line.startswith("@@"):
                        payload = line[2:]
                info = json.loads(payload) if payload else {}
            except Exception as exc:
                info = {"error": str(exc)}
            self.msg_q.put(("env", info))

        threading.Thread(target=work, daemon=True).start()

    def install_env(self):
        if self.install_thread and self.install_thread.is_alive():
            messagebox.showinfo("提示", "环境安装正在进行中，请稍候。")
            return
        ok = messagebox.askyesno(
            "一键装环境",
            "将在本面板目录创建 .venv 虚拟环境并安装：\n\n"
            "  · PyTorch + torchvision（CUDA 12.8 版，约 2~3 GB）\n"
            "  · av、pillow\n\n"
            "首次下载耗时较长，期间可以看日志。是否继续？")
        if not ok:
            return
        base_py = find_system_python()
        if not base_py:
            messagebox.showerror(
                "缺少 Python",
                "本机没有检测到 Python 解释器，无法自动创建运行环境。\n\n"
                "请先安装 Python 3.11（安装时务必勾选 Add Python to PATH）：\n"
                "https://www.python.org/downloads/\n\n"
                "装好后再回到面板点「一键装环境」即可。")
            return
        self._set_busy(True)
        self.var_status.set("正在安装依赖...")

        def work():
            venv_dir = os.path.join(BASE_DIR, ".venv")
            venv_py = os.path.join(venv_dir, "Scripts", "python.exe")
            steps = []
            if not os.path.exists(venv_py):
                self.msg_q.put(("log", "创建虚拟环境：%s" % venv_dir))
                steps.append([base_py, "-m", "venv", venv_dir])
            py = venv_py if os.path.exists(venv_py) else base_py
            steps += [
                [py, "-m", "pip", "install", "--upgrade", "pip"],
                [py, "-m", "pip", "install", "torch", "torchvision",
                 "--index-url", "https://download.pytorch.org/whl/cu128"],
                [py, "-m", "pip", "install", "av", "pillow", "numpy"],
            ]
            ok_all = True
            for cmd in steps:
                self.msg_q.put(("log", "$ " + " ".join(cmd[-3:])))
                try:
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace",
                                         creationflags=no_window_flags())
                    for line in p.stdout:
                        line = line.rstrip()
                        if line:
                            self.msg_q.put(("log", line))
                    p.wait()
                    if p.returncode != 0:
                        ok_all = False
                        self.msg_q.put(("log", "命令失败，退出码 %s" % p.returncode))
                        break
                except Exception as exc:
                    ok_all = False
                    self.msg_q.put(("log", "执行异常：%s" % exc))
                    break
            self.msg_q.put(("install_done", ok_all))

        self.install_thread = threading.Thread(target=work, daemon=True)
        self.install_thread.start()

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------
    def start_job(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        inp = self.var_input.get().strip().strip('"')
        if not inp or not os.path.exists(inp):
            messagebox.showwarning("缺少素材", "请先选择要抠像的视频文件。")
            return
        outdir = self.var_outdir.get().strip() or DEFAULT_OUT
        if not (self.var_comp.get() or self.var_fgr.get() or self.var_pha.get()):
            messagebox.showwarning("输出为空", "至少要勾选一项输出内容（合成画面 / 前景 / Alpha）。")
            return

        py = pick_python()
        if not py or not os.path.exists(py):
            messagebox.showerror(
                "缺少 Python",
                "没有可用的 Python 解释器，无法启动推理。\n\n"
                "请先安装 Python 3.11（勾选 Add Python to PATH），\n"
                "再点「一键装环境」装好 GPU 依赖。")
            return
        info = self.env_info
        if info.get("torch") in (None, "缺失"):
            if not messagebox.askyesno(
                    "依赖未就绪",
                    "当前解释器还没装 PyTorch，无法推理。\n\n"
                    "可以先点「一键装环境」，或仍要尝试启动（大概率失败）。继续吗？"):
                return

        try:
            ratio = float(self.var_ratio.get())
        except Exception:
            ratio = 0.5

        bg = self.var_bg.get().strip().strip('"')
        if bg and not os.path.exists(bg):
            messagebox.showwarning("背景文件不存在",
                                   "背景素材路径无效：\n%s\n\n请重新选择，或点「清空」走绿幕。" % bg)
            return

        # 双重保险：无论如何都只把合法模型名交给 worker
        variant = VARIANT_MAP.get(self.var_variant_text.get(), self.var_variant.get() or "resnet50")
        if variant not in ("resnet50", "mobilenetv3"):
            variant = "resnet50"

        local_repo = os.path.join(BASE_DIR, "rvm_repo")
        cfg = {
            "input": inp,
            "output_dir": outdir,
            "variant": variant,
            "background": bg,
            "downsample_ratio": ratio,
            "seq_chunk": int(self.var_chunk.get() or 12),
            "out_composition": bool(self.var_comp.get()),
            "out_foreground": bool(self.var_fgr.get()),
            "out_alpha": bool(self.var_pha.get()),
            "output_type": self.var_png.get(),
            "video_mbps": int(self.var_mbps.get() or 8),
            "max_seconds": float(self.var_trial.get() or 0),
            "device": "cuda",
            "repo_dir": local_repo if os.path.isdir(local_repo) else "",
        }
        os.makedirs(outdir, exist_ok=True)
        cfg_path = os.path.join(outdir, "_last_job.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self._save_config()

        self.log_line("─" * 46)
        self.log_line("开始任务：%s" % os.path.basename(inp))
        self.log_line("解释器：%s" % py)
        self.log_line("模型档位：%s" % variant)
        self.log_line("背景：%s" % (os.path.basename(bg) if bg else "纯绿幕"))
        self.bar.configure(value=0)
        self.lbl_pct.configure(text="0%")
        self._set_busy(True)
        self.var_status.set("正在启动推理进程...")

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        # 把解压目录（即 exe 所在目录）显式传给 worker，
        # 便于它在同目录找到随包自带的 ffmpeg.exe / ffprobe.exe
        env["QINGSONG_APP_DIR"] = BASE_DIR
        cmd = [py, "-u", WORKER, "--config", cfg_path]

        def work():
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=no_window_flags(), env=env, cwd=BASE_DIR)
                for line in self.proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    if line.startswith("@@PROGRESS"):
                        parts = line[10:].strip().split("|")
                        try:
                            pct = float(parts[0])
                            speed = float(parts[1]) if len(parts) > 1 else 0.0
                            done = int(parts[2]) if len(parts) > 2 else 0
                            total = int(parts[3]) if len(parts) > 3 else 0
                        except Exception:
                            continue
                        self.msg_q.put(("progress", (pct, speed, done, total)))
                    elif line.startswith("@@LOG"):
                        self.msg_q.put(("log", line[5:].strip()))
                    elif line.startswith("@@DONE"):
                        self.msg_q.put(("done", line[6:].strip()))
                    elif line.startswith("@@ERROR"):
                        self.msg_q.put(("error", line[7:].strip()))
                    else:
                        self.msg_q.put(("log", line))
                self.proc.wait()
                self.msg_q.put(("exit", self.proc.returncode))
            except Exception as exc:
                self.msg_q.put(("error", "启动失败：%s" % exc))

        self.worker_thread = threading.Thread(target=work, daemon=True)
        self.worker_thread.start()

    def stop_job(self):
        if not self.proc:
            return
        try:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=no_window_flags())
            self.log_line("已请求停止。")
        except Exception as exc:
            self.log_line("停止失败：%s" % exc)

    # ------------------------------------------------------------------
    # 消息泵
    # ------------------------------------------------------------------
    def pump(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self.log_line(payload)
                elif kind == "progress":
                    pct, speed, done, total = payload
                    self.bar.configure(value=pct)
                    self.lbl_pct.configure(text="%.1f%%" % pct)
                    msg = "处理中 · %.1f 帧/秒" % speed
                    if total:
                        msg += " · %d/%d 帧" % (min(done, total), total)
                    else:
                        msg += " · 已处理 %d 帧" % done
                    if total and speed > 0.05 and done < total:
                        msg += " · 剩余约 %s" % fmt_duration((total - done) / speed)
                    self.var_status.set(msg)
                elif kind == "done":
                    self.var_status.set("完成 · 输出在 %s" % payload)
                    self.bar.configure(value=100)
                    self.lbl_pct.configure(text="100%")
                    self._set_busy(False)
                    if messagebox.askyesno("抠像完成", "处理完成，是否打开输出目录？"):
                        self.open_outdir()
                elif kind == "error":
                    self.log_line("[错误] " + payload)
                    self.var_status.set("出错了，详见日志")
                elif kind == "exit":
                    self._set_busy(False)
                    if payload == 0:
                        self.var_status.set("已完成")
                    else:
                        self.var_status.set("进程退出，退出码 %s（详见日志）" % payload)
                elif kind == "env":
                    self.env_info = payload or {}
                    self._render_env()
                elif kind == "install_done":
                    self._set_busy(False)
                    self.var_status.set("依赖安装完成" if payload else "依赖安装未完成")
                    self.log_line("环境安装%s。" % ("完成" if payload else "中途失败"))
                    self.detect_env_async()
        except queue.Empty:
            pass
        self.after(120, self.pump)

    def _render_env(self):
        info = self.env_info
        if info.get("error"):
            self.var_env.set("环境未就绪：%s" % info.get("error"))
            self.log_line("环境提示：%s —— 请先安装 Python 3.11，再点「一键装环境」。"
                          % info.get("error"))
            return
        torch_v = info.get("torch", "缺失")
        cuda = "CUDA就绪" if info.get("cuda") else "CUDA不可用"
        gpu = info.get("gpu") or ""
        ff = "ffmpeg就绪" if info.get("ffmpeg") else "ffmpeg缺失"
        self.var_env.set("Python %s | torch %s | %s%s | %s" % (
            info.get("python", "?"), torch_v, cuda,
            ("(" + gpu + ")") if gpu else "", ff))
        if info.get("torch") in (None, "缺失"):
            self.log_line("环境提示：PyTorch 未安装，点「一键装环境」即可自动装好。")
        elif not info.get("cuda"):
            self.log_line("环境提示：torch 已装但 CUDA 不可用，请确认装的是 cu128 版本。")

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.var_outdir.set(cfg.get("output_dir", DEFAULT_OUT))
            self.var_preset.set(cfg.get("preset", PRESETS[0][0]))
            self.var_ratio.set(str(cfg.get("downsample_ratio", 0.5)))
            self.var_chunk.set(str(cfg.get("seq_chunk", 12)))
            self.var_mbps.set(str(cfg.get("video_mbps", 8)))
            self.var_png.set(cfg.get("output_type", "video"))
            self.var_trial.set(str(cfg.get("max_seconds", 0)))
            raw = str(cfg.get("variant", "resnet50")).lower()
            variant = "mobilenetv3" if "mobilenet" in raw else "resnet50"
            label = "mobilenetv3（更快）" if variant == "mobilenetv3" else "resnet50（边缘更细，推荐）"
            self.var_variant.set(variant)
            self.var_variant_text.set(label)
            bg = cfg.get("background", "") or ""
            self.var_bg.set(bg if (bg and os.path.exists(bg)) else "")
        except Exception:
            pass

    def _save_config(self):
        cfg = {
            "output_dir": self.var_outdir.get(),
            "preset": self.var_preset.get(),
            "downsample_ratio": self.var_ratio.get(),
            "seq_chunk": self.var_chunk.get(),
            "video_mbps": self.var_mbps.get(),
            "output_type": self.var_png.get(),
            "max_seconds": self.var_trial.get(),
            "variant": self.var_variant.get(),
            "background": self.var_bg.get(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        if self.worker_thread and self.worker_thread.is_alive():
            if not messagebox.askyesno("正在处理", "抠像还在进行中，确定要退出吗？进程会被终止。"):
                return
            self.stop_job()
        self._save_config()
        self.destroy()


def main():
    try:
        app = RvmPanel()
        app.pump()
        app.mainloop()
    except Exception:
        err = os.path.join(BASE_DIR, "panel_error.log")
        with open(err, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
