---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ebc98092bcde066fca4114d7810e399_86336073d9e411f1ba6252540064ee0f
    ReservedCode1: glpzvQt7b2kk7sOE/+ccidNea/BoIZtxtzMAFHCOjRTCfSYJA991tDrDhuA/ja/Zx8g4bKwlASokhOLuB1KqngoN16E5VWL4NqclBmq/JulXeUPBM/OGaeJJuudFm3qZxMg2m7J5hSbQQaWLts7WWly/NlYcsJUiXuAdaGzX57RYMHSke9v4tDBxQaE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ebc98092bcde066fca4114d7810e399_86336073d9e411f1ba6252540064ee0f
    ReservedCode2: glpzvQt7b2kk7sOE/+ccidNea/BoIZtxtzMAFHCOjRTCfSYJA991tDrDhuA/ja/Zx8g4bKwlASokhOLuB1KqngoN16E5VWL4NqclBmq/JulXeUPBM/OGaeJJuudFm3qZxMg2m7J5hSbQQaWLts7WWly/NlYcsJUiXuAdaGzX57RYMHSke9v4tDBxQaE=
---

# 青松口播抠像面板 · Qingsong RVM Panel

> **不想折腾环境？直接下解压即用包**
> 👉 [Releases 下载 qingsong-rvm-panel-v1.0.1-win64.zip](https://github.com/tiandao2025/qingsong-rvm-panel/releases/latest)
> 解压 → 双击 `一键装环境.bat`（装一次依赖，约 2~3 GB）→ 双击 `qingsong-rvm-panel.exe` 开始抠像。
> 面板本身已打包成单个 exe（无需 Python 即可打开界面），只有 GPU 依赖需要一次性安装。

一个跑在 Windows 本地的**口播视频一键抠像**小工具：不上传云端、不排队、不限时长、无水印，全部在一张显卡上完成。

面板只是「遥控器」，真正的视频抠像由 [RVM（Robust Video Matting）](https://github.com/PeterL1n/RobustVideoMatting) + PyTorch + CUDA 在本地完成。

- 输入：一段口播 / 人物视频
- 输出：绿幕合成片、纯黑底前景、Alpha 蒙版（或 PNG 序列），也可直接把人像合到你自己的背景图 / 背景视频上

---

## 功能特点

| 能力 | 说明 |
| --- | --- |
| 本地离线 | 视频不出本机，无上传、无账号、无时长限制 |
| 实时进度与日志 | 处理进度、帧率、剩余时间、错误信息滚动可见 |
| 场景预设 | 1080P 半身人像 / 走动全身两档下采样比，避免手动试参数 |
| 两种模型档位 | `resnet50` 边缘更细（推荐出片）、`mobilenetv3` 速度快一倍 |
| 多路输出 | 合成画面（绿幕底）/ 前景 / Alpha，可任意组合 |
| 背景合成 | 直接指定背景图片或背景视频，人像逐帧贴合，免去剪映二次合成 |
| 试跑剪样 | 设置只处理前 N 秒，先验证效果再跑长片 |
| 设置记忆 | 参数保存在 `panel_config.json`，下次打开自动恢复 |
| 零安装绿色使用 | 删掉文件夹即卸载，不写注册表、不装系统服务 |

---

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（64 位） |
| 显卡 | 推荐 NVIDIA 独显，显存 8GB 起（1080P 口播 16GB 更稳）；纯 CPU 可运行但非常慢 |
| **RTX 50 系显卡** | **必须使用 CUDA 12.8（cu128）版 PyTorch**，旧版本 CUDA 不支持 Blackwell 架构，会报设备内核不存在 |
| 磁盘 | 约 6GB（`.venv` 环境 4~5GB，模型权重约 120MB） |
| Python | 无需预装。面板会用系统 Python 创建独立 `.venv` |

> 说明：面板的「一键装环境」固定从 `https://download.pytorch.org/whl/cu128` 拉取 PyTorch，因此 RTX 50 系开箱即用；其他显卡同样可以跑 cu128 版。

---

## 快速开始

### 1. 获取代码

```powershell
git clone https://github.com/tiandao2025/qingsong-rvm-panel.git
cd qingsong-rvm-panel
```

也可以直接下载仓库 ZIP 解压（GitHub 页面 `Code` → `Download ZIP`）。**路径建议不要包含空格或特殊符号**，解压到 `D:\tools\rvm-panel` 这类纯英文目录最省事。

### 2. 一键装环境

双击 **`启动抠像面板.bat`** 打开面板，点顶部「**一键装环境**」，确认后等待完成。

面板会自动执行：

1. 在项目目录创建 `.venv` 虚拟环境
2. 升级 pip
3. 安装 `torch` + `torchvision`（**CUDA 12.8 / cu128** 版）
4. 安装 `av`、`pillow`、`numpy`

首次约 2~3GB 下载，视网速 10~30 分钟。装完右下角状态显示「CUDA就绪」即成功；若显示「CUDA不可用」，说明装成了 CPU 版，删除 `.venv` 后重新点一键装环境。

### 3. 准备 RVM 源码与模型权重

面板本身不含推理引擎，需要把 [RVM 官方仓库](https://github.com/PeterL1n/RobustVideoMatting) 克隆到本项目的 `rvm_repo\` 目录（因体积与许可证原因，该目录不随本仓库发布）：

```powershell
git clone https://github.com/PeterL1n/RobustVideoMatting.git rvm_repo
```

再下载模型权重放进 `rvm_repo\` 根目录（该目录已有 `model\`、`inference.py` 等源码，缺的只是 `.pth` 权重）：

| 权重文件 | 大小 | 说明 |
| --- | --- | --- |
| `rvm_repo\rvm_resnet50.pth` | 103MB | 边缘最细、发丝级效果，**推荐出片**，面板默认档位 |
| `rvm_repo\rvm_mobilenetv3.pth` | 14.5MB | 速度快一倍，边缘略软，适合快速预览 |

下载页（官方 releases，v1.0.0）：<https://github.com/PeterL1n/RobustVideoMatting/releases/tag/v1.0.0>

在页面资源列表里取 `rvm_resnet50.pth` / `rvm_mobilenetv3.pth`，放进 `rvm_repo\` 即可。只想先跑通流程，只下 14.5MB 的 `mobilenetv3`，并把面板「模型精度」切成 `mobilenetv3`。

最终目录应为：

```
rvm-panel\
├─ rvm_repo\
│  ├─ model\                  RVM 网络结构
│  ├─ inference.py            官方推理脚本
│  ├─ rvm_resnet50.pth        ← 手动下载
│  └─ rvm_mobilenetv3.pth     ← 手动下载
└─ rvm_panel.py
```

### 4. 开始抠像

1. **原视频** → 点「浏览...」选中口播素材
2. **输出目录** → 默认 `output\`，可改
3. **试跑时长** → 第一次填 `10`，先看 10 秒效果
4. 点「开始抠像」，进度条与日志实时刷新
5. 满意后把试跑时长改回 `0`，跑完整条

---

## 参数怎么选

| 参数 | 建议 |
| --- | --- |
| 场景预设 | 1080P 半身口播选「人像」档（下采样比 0.25）；走动 / 全身选另一档（0.4） |
| 模型精度 | `resnet50` 出成片；`mobilenetv3` 快速预览 |
| 下采样比 | 跟预设走即可。0.5 左右常是口播的最佳平衡点（边缘干净、背景残留少）；越小越快越糊 |
| 并行帧数 | 16GB 显存 1080P 填 12 稳定；报显存不足降到 4 |
| 码率 | 8 Mbps 够用，追求画质填 16 |
| 试跑时长 | 0 = 整条视频；其它数字 = 只处理前 N 秒 |

输出勾选项：

| 勾选项 | 产出文件 | 用途 |
| --- | --- | --- |
| 合成画面 | `xxx_comp.mp4` | 绿幕底，丢进剪映做色度键抠图最省事 |
| 前景 | `xxx_fgr.mp4` | 只有人，纯黑底，用于后期叠加 |
| Alpha | `xxx_alpha.mp4` | 灰度蒙版，给专业合成软件用 |

**原声自动保留**：输出 MP4 时程序会用 ffmpeg 把原片音轨合进每个 mp4（视频流不重编码，音频转 AAC 192k）；输出 PNG 序列时在同目录生成同名 `.m4a` 原声文件。不再需要手动合音轨。

### 想要真正带透明通道的成片

MP4 装不下透明通道，直接输出 alpha 视频会变黑底。正确做法：

1. 输出格式切到「**PNG 序列**」
2. 勾选「前景」+「Alpha」
3. 跑完得到 `comp\` `fgr\` `pha\` 三个文件夹
4. 用 ffmpeg 合成透明 WebM：

```powershell
ffmpeg -framerate 30 -i pha\%06d.png -i fgr\%06d.png -filter_complex "[1:v][0:v]alphamerge,format=yuva420p" -c:v libvpx-vp9 -pix_fmt yuva420p 透明成品.webm
```

---

## 目录结构

```
rvm-panel\
├─ 启动抠像面板.bat           双击启动（无黑框控制台）
├─ 启动抠像面板-调试.bat       排错用，能看到完整报错
├─ rvm_panel.py              面板界面（tkinter，零第三方依赖）
├─ rvm_worker.py             后台推理进程（需 .venv 中的 torch）
├─ assets\                   品牌资源（logo 原图 / 徽标 / 窗口图标）
├─ qingsong_rvm.ico          桌面快捷方式图标
├─ rvm_repo\                 RVM 上游源码与权重（需按第 3 步自行克隆，不入本仓库）
├─ demo\                     效果示例（不随仓库发布）
├─ panel_config.json         参数记忆（首次运行自动生成，不入库）
└─ .venv\                    虚拟环境（一键装环境后生成，不入库）
```

---

## 常见问题

**Q：点了开始，一闪就退出 / 立刻报错？**
用 `启动抠像面板-调试.bat` 启动看完整报错。多数情况是 `.venv` 里的 torch 没装好，先点「环境自检」。

**Q：提示 CUDA out of memory？**
「并行帧数」从 12 降到 4，或下采样比调小一档；长视频先用「试跑时长」抽样验证。

**Q：输出视频没有声音？**
v1.0.1 起已默认**自动保留原声**，正常情况无需处理。排查顺序：

1. 日志里出现「已合入原音频」即成功，直接使用 `xxx_comp.mp4`；
2. 日志提示「原视频无音频流」表示素材自身无声，属正常跳过；
3. 日志提示「未找到 ffmpeg」表示 PATH 与常见安装路径均未找到 ffmpeg，装好并加入 PATH 后重跑；
4. 输出格式为 PNG 序列时，原声是同目录的同名 `xxx.m4a`，剪映中拖到同轨对齐即可。

个别文件合入失败只影响该文件，其余输出照常。需要手工合成仍可用：

```powershell
ffmpeg -i xxx_comp.mp4 -i 原片.mp4 -c copy -map 0:v:0 -map 1:a:0 -shortest 成品.mp4
```

**Q：开头一两秒边缘在飘？**
RNN 记忆前几帧还在建立，属正常现象。成片从第 5 帧往后用，或在素材前垫 0.5 秒空镜。

**Q：处理速度大概多快？**
RTX 5060 Ti 实测（704×1280 竖屏、97 帧素材）：

| 档位 | 速度 |
| --- | --- |
| resnet50 + 下采样比 1.0 | 约 6.2 帧/秒（最慢最细） |
| resnet50 + 下采样比 0.5 | 约 11.3 帧/秒（推荐） |
| mobilenetv3 + 下采样比 0.25 | 约 9.7 帧/秒（快，边缘偏虚） |

折算：25fps 的 1 分钟口播，`resnet50 + 0.5` 约 2.5 分钟出片。

**Q：怎么卸载？**
直接删除整个文件夹即可，不写注册表、不装服务。

**Q：桌面图标换了没生效？**
Windows 图标缓存问题，重启资源管理器或重新登录即可刷新。

---

## 许可证与致谢

- 本项目主程序遵循 **GNU GPLv3**（因集成以 GPLv3 发布的 RVM 代码）。
- `rvm_repo\` 目录来自 [PeterL1n/RobustVideoMatting](https://github.com/PeterL1n/RobustVideoMatting)，版权归原作者，遵循其仓库内的 **GPLv3** 许可证，原始说明见 `rvm_repo\README.md`。
- 界面与品牌图标由本项目自制。

> 使用本工具处理素材时，请自行确认对素材拥有合法使用权，并遵守当地法律法规。

---

*青松设计 · 本地 AI 工具系列*
*（内容由AI生成，仅供参考）*
