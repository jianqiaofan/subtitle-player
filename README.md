# 字幕学习播放器 (Subtitle Player)

基于 **Python + PyQt6** 的字幕播放器，集成本地 Whisper 转写、边播边转、AI 笔记等功能。适合语言学习、课程视频复习。

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![PyQt6](https://img.shields.io/badge/PyQt6-6.6+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## 功能特性

- **字幕播放器**：左右布局，左侧视频/音频，右侧字幕列表；点击字幕跳转，播放时高亮同步
- **多语种字幕**：同目录自动匹配 `视频名_语种.srt`，下拉切换
- **边播边转**：播放同时按语音停顿分片转写（原文混排），输出 `视频名_同步.srt`
- **续转未完成字幕**：检测到未完成的同步字幕后自动续转
- **全量转写工具**：批量离线转写，支持多语种与原文混排
- **AI 笔记**：调用 [DeepSeek API](https://api-docs.deepseek.com/zh-cn/) 将字幕整理为 Markdown 笔记
- **查看笔记**：一键打开 `视频名_AI笔记.md`

## 环境要求

| 依赖 | 说明 |
|------|------|
| Windows | 启动脚本为 `.bat`（其他系统需自行适配） |
| Python 3.10+ | 推荐 3.11 / 3.12 |
| FFmpeg | 加入系统 PATH，用于音频提取 |
| Whisper GGML 模型 | `.bin` 文件，**需自行下载**，勿提交到 Git |

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/你的用户名/subtitle-player.git
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

### 3. 首次配置

```bash
首次配置.bat
```

将 `config.json.example` 复制为 `config.json`，然后编辑：

| 字段 | 说明 |
|------|------|
| `model_path` | Whisper `.bin` 模型绝对路径 |
| `deepseek_api_key` | [DeepSeek API Key](https://platform.deepseek.com/api_keys)（AI 笔记功能需要） |
| `deepseek_model` | 默认 `deepseek-v4-flash`，可改为 `deepseek-v4-pro` |

> **注意**：`config.json` 含私密信息，已在 `.gitignore` 中排除，请勿提交到 Git。

### 4. 准备 Whisper 模型

下载 GGML 格式 Whisper 模型（如 `ggml-medium.bin`），重命名或配置路径指向该文件。  
模型文件体积较大（数百 MB～GB），请放在仓库外或 Release 中提供下载链接。

### 5. 启动

```bash
启动.bat          # 字幕播放器（主程序）
启动转写工具.bat   # 仅全量转写工具
```

## 字幕文件命名规则

| 类型 | 文件名示例 |
|------|-----------|
| 固定语种 | `课程名_中文.srt` |
| 原文混排（全量转写） | `课程名_原文混排(多语言).srt` |
| 边播边转 | `课程名_同步.srt` |
| AI 笔记 | `课程名_AI笔记.md` |

## 项目结构

```
subtitle-player/
├── subtitle_app/          # 主程序
│   ├── main.py            # 入口（默认启动播放器）
│   ├── config.json.example
│   ├── gui/               # PyQt6 界面
│   └── core/              # 转写、字幕、AI 笔记等核心逻辑
├── examples/              # 示例字幕
├── LICENSE
└── README.md
```

## 使用说明

### 打开媒体

- 点击「打开媒体」或拖入视频/音频
- 若同目录有有效字幕，自动加载
- 若存在未完成的 `视频名_同步.srt`，自动续转

### 边播边转

- 点击「边播边转」选择字幕来源
- 默认原文混排，长视频按 10 分钟窗口分析语音结构
- 无独立显卡亦可运行（CPU 推理）

### AI 笔记

- 收集同目录所有有效字幕作为语料
- 生成 Markdown 笔记保存至视频同目录
- 需配置 DeepSeek API Key

## 技术栈

- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — GUI 与媒体播放
- [pywhispercpp](https://github.com/absadiki/pywhispercpp) — 本地 Whisper 推理（CPU）
- [DeepSeek API](https://api-docs.deepseek.com/zh-cn/) — AI 笔记（OpenAI 兼容格式）
- FFmpeg — 音频提取与语音分片

## 开源协议

本项目采用 [MIT License](LICENSE)。

Whisper 模型及第三方库遵循其各自许可协议。

## 贡献

欢迎提交 Issue 与 Pull Request。

## 安全提示

- 切勿将 `config.json` 或 API Key 提交到公开仓库
- 若密钥曾意外泄露，请立即在 DeepSeek 平台作废并重新生成
