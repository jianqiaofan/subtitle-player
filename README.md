# 字幕学习播放器 (Subtitle Player)

基于 **Python + PyQt6** 的字幕播放器，集成本地 Whisper 转写、边播边转、AI 笔记、生词表，以及基于 yt-dlp 的视频下载工具。适合语言学习、课程视频复习。

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![PyQt6](https://img.shields.io/badge/PyQt6-6.6+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## 功能特性

### 字幕播放器

- **左右布局**：左侧视频/音频，右侧字幕列表；点击字幕跳转，播放时高亮同步
- **多语种字幕**：同目录自动匹配 `视频名_语种.srt`，下拉切换
- **字幕编辑**：右键编辑时间轴与文本；选中文字可调用外部翻译软件（当前适配百度翻译电脑版）
- **重复播放**：右键单条字幕循环播放，便于跟读
- **倍速与倒计时**：播放倍速可调；学习倒计时仅在窗口位于最前时读秒
- **查看后台**：启动后默认隐藏命令行窗口，需要时再显示日志

### 转写与学习

- **边播边转**：播放同时按语音停顿分片转写（原文混排），输出 `视频名_同步.srt`
- **续转未完成字幕**：检测到未完成的同步字幕后自动续转
- **全量转写工具**：批量离线转写，支持多语种与原文混排；可选 CPU 或 CUDA GPU
- **AI 笔记**：调用 [DeepSeek API](https://api-docs.deepseek.com/zh-cn/) 将字幕整理为 Markdown 笔记
- **生词表**：从字幕提取英语 / 日语 / 中文词汇，导出 Markdown 与 CSV（可导入 Anki）
- **纯文字**：将字幕导出为无时间轴的 Markdown 文本

### 视频下载

- 从播放器 **工具 → 视频下载** 打开，也可单独运行 `video_downloader/启动.bat`
- 基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，支持 B 站、YouTube 等站点
- **解析单视频** / **解析合集**：B 站单集若属于 UP 主合集，可一键展开全部剧集
- 任务队列：进度、速度、剩余时间；可同时下载多个任务
- 可选：下载字幕、封面、写入元数据、代理、限速、从浏览器读取 Cookie

> 请仅下载你有权保存的内容，遵守各站点服务条款与当地法律法规。

## 环境要求

| 依赖 | 说明 |
|------|------|
| Windows | 启动脚本为 `.bat`（其他系统需自行适配） |
| Python 3.10+ | 推荐 3.11 / 3.12 |
| FFmpeg | 加入系统 PATH，用于音频提取与下载后的音视频合并 |
| Whisper GGML 模型 | `.bin` 文件，**需自行下载**，勿提交到 Git |

GPU 加速为可选项：需安装 NVIDIA CUDA Toolkit，再运行 `安装CUDA推理.bat` 编译 CUDA 版 pywhispercpp。

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/jianqiaofan/subtitle-player.git
cd subtitle-player
```

### 2. 安装 Python 依赖

```bash
cd subtitle_app
安装依赖.bat
```

或手动：

```bash
pip install -r requirements.txt
```

该文件已包含播放器、转写与视频下载工具的依赖。若只使用下载器，也可进入 `video_downloader` 后安装其 `requirements.txt`。

### 3. 首次配置

```bash
首次配置.bat
```

将 `config.json.example` 复制为 `config.json`，然后编辑 Whisper 模型路径等。

**AI 笔记（DeepSeek）** 可在播放器内点击 **设置 → 大模型配置** 填写 API Key，无需手动编辑 JSON。

| 字段 | 说明 |
|------|------|
| `model_path` | Whisper `.bin` 模型绝对路径 |
| `inference_device` | `auto` / `cpu` / `gpu`，也可在播放器设置中切换 |
| `deepseek_api_key` | 可在软件「大模型配置」中填写 |
| `deepseek_model` | 默认 `deepseek-v4-flash`，可改为 `deepseek-v4-pro` |
| `translate_hotkey` | 外部翻译热键，默认 `Ctrl+Alt+C`，需与百度翻译中的设置一致 |

> **注意**：`config.json` 含私密信息，已在 `.gitignore` 中排除，请勿提交到 Git。

### 4. 准备 Whisper 模型

下载 GGML 格式 Whisper 模型（如 `ggml-medium.bin`），重命名或配置路径指向该文件。  
模型文件体积较大（数百 MB～GB），请放在仓库外或 Release 中提供下载链接。

### 5. 启动

```bash
启动.bat                    # 字幕播放器（主程序）
启动转写工具.bat             # 仅全量转写工具
video_downloader\启动.bat    # 仅视频下载工具
```

启动脚本会隐藏命令行窗口。若转写或下载出错，可在界面中点击 **查看后台** 查看日志。

## 使用说明

### 打开媒体

- 点击「打开媒体」或拖入视频/音频
- 若同目录有有效字幕，自动加载
- 若存在未完成的 `视频名_同步.srt`，自动续转

### 边播边转

- **工具 → 边播边转** 选择字幕来源
- 默认原文混排，长视频按 10 分钟窗口分析语音结构
- 无独立显卡亦可运行（CPU 推理）；有 NVIDIA 显卡时可在 **设置 → 推理设备** 中选择 GPU

### 字幕编辑与翻译

- 在字幕列表右键：**编辑** / **复制** / **重复播放**
- 编辑对话框中选中文字后右键「翻译」，会向系统发送热键，交给已打开的百度翻译电脑版
- 热键在 **设置 → 翻译热键** 中配置，须与百度翻译里的「快捷键发起翻译」完全一致

### AI 笔记

- 首次使用请打开 **设置 → 大模型配置** 填写 DeepSeek API Key
- 收集同目录所有有效字幕作为语料
- 生成 Markdown 笔记保存至视频同目录

### 生词表与纯文字

- **工具 → 生词表**：按语言提取词汇，生成 `视频名_生词表_英语.md` 与同名 CSV
- **工具 → 纯文字**：导出无时间轴的 Markdown，便于阅读或喂给其他模型

### 视频下载

1. 在播放器中选择 **工具 → 视频下载**，或运行 `subtitle_app/video_downloader/启动.bat`
2. 粘贴视频链接，点 **解析单视频** 或 **解析合集**
3. 在任务列表中勾选后点 **开始下载选中**
4. 在 **设置** 中指定保存目录；需要登录的内容可填写 Cookie 或选择从浏览器读取

B 站合集：粘贴其中一集的链接后点「解析合集」，会展开该 UP 主合集中的全部剧集。  
YouTube：若解析失败，可在设置中配置代理，或从浏览器导入 Cookie。

下载器配置保存在 `video_downloader/config/user_settings.json`（已加入 `.gitignore`）。

## 字幕文件命名规则

| 类型 | 文件名示例 |
|------|-----------|
| 固定语种 | `课程名_中文.srt` |
| 原文混排（全量转写） | `课程名_原文混排(多语言).srt` |
| 边播边转 | `课程名_同步.srt` |
| AI 笔记 | `课程名_AI笔记.md` |
| 生词表 | `课程名_生词表_英语.md` / `.csv` |
| 纯文字 | `课程名_纯文字.md` |

## 项目结构

```
subtitle-player/
├── subtitle_app/                 # 主程序
│   ├── main.py                   # 入口（默认启动播放器，--transcribe 启动转写）
│   ├── config.json.example
│   ├── 启动.bat / 启动转写工具.bat / 安装依赖.bat
│   ├── 安装CUDA推理.bat          # 可选：编译 GPU 版 Whisper
│   ├── gui/                      # PyQt6 界面
│   ├── core/                     # 转写、字幕、AI 笔记、翻译热键等
│   └── video_downloader/         # 视频下载工具（CustomTkinter + yt-dlp）
│       ├── main.py
│       ├── 启动.bat
│       ├── app/                  # 队列、下载、合集解析、界面
│       └── config/               # 默认设置；用户设置不入库
├── examples/                     # 示例字幕
├── LICENSE
└── README.md
```

## 技术栈

- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — 播放器 GUI 与媒体播放
- [pywhispercpp](https://github.com/absadiki/pywhispercpp) — 本地 Whisper 推理（CPU / 可选 CUDA）
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — 视频下载
- [DeepSeek API](https://api-docs.deepseek.com/zh-cn/) — AI 笔记（OpenAI 兼容格式）
- FFmpeg — 音频提取、语音分片、下载后的封装合并

## 开源协议

本项目采用 [MIT License](LICENSE)。

Whisper 模型、yt-dlp 及第三方库遵循其各自许可协议。请合法使用下载功能。

## 贡献

欢迎提交 Issue 与 Pull Request。

## 安全提示

- 切勿将 `config.json`、`user_settings.json` 或 API Key 提交到公开仓库
- 若密钥曾意外泄露，请立即在 DeepSeek 平台作废并重新生成
- Cookie 文件含登录凭据，请只保存在本机，不要分享或入库
