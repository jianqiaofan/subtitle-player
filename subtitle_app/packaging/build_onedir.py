"""Build a CPU-only onedir folder and place models plus ffmpeg beside the exe."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
SPEC = Path(__file__).resolve().parent / "subtitle_player.spec"
DIST = APP / "dist" / "字幕播放器"
MODELS_SRC = REPO / "models"

WHISPER_MODEL_FILES = (
    "win系统模型最小.bin",
    "win系统模型中等.bin",
    "win系统模型最大.bin",
)

README = """字幕播放器（CPU 转字幕）

双击「字幕播放器.exe」即可使用。播放、打标签、翻译、视频下载、AI 笔记、生词表都在这个程序里。

转字幕只使用 CPU。请先用「工具 → 音视频转字幕」生成字幕，再播放。

模型文件默认放在本目录的 models 文件夹：
  win系统模型最小.bin
  win系统模型中等.bin
  win系统模型最大.bin

如果 models 文件夹里没有这三个文件，打开「音视频转字幕」，点击「浏览」指定你自己的 .bin 模型路径。

请不要把别人的 config.json 拷过来。第一次启动会在本目录生成新的 config.json，API 密钥请在程序里自己填写。
"""


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    try:
        os.link(src, dest)
        print(f"硬链接 {dest.name}")
    except OSError:
        print(f"复制 {dest.name}")
        shutil.copy2(src, dest)


def _place_models() -> None:
    dest_dir = DIST / "models"
    dest_dir.mkdir(parents=True, exist_ok=True)
    found = 0
    for name in WHISPER_MODEL_FILES:
        src = MODELS_SRC / name
        if not src.is_file():
            print(f"未找到模型，打包后需手动放入 models：{src}")
            continue
        _link_or_copy(src, dest_dir / name)
        found += 1
    print(f"models 目录已放置 {found} 个模型")


def _place_ffmpeg() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("未在 PATH 中找到 ffmpeg/ffprobe，安装包里不会自带。")
        return
    folder = DIST / "ffmpeg"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ffmpeg, folder / "ffmpeg.exe")
    shutil.copy2(ffprobe, folder / "ffprobe.exe")
    print(f"已复制 ffmpeg：{ffmpeg}")


def main() -> int:
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"]
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=APP)
    if result.returncode != 0:
        return result.returncode
    if not (DIST / "字幕播放器.exe").is_file():
        print(f"未找到输出程序：{DIST}")
        return 1
    _place_models()
    _place_ffmpeg()
    example = APP / "config.json.example"
    if example.is_file():
        shutil.copy2(example, DIST / "config.json.example")
    (DIST / "使用说明.txt").write_text(README, encoding="utf-8")
    user_config = DIST / "config.json"
    if user_config.is_file():
        user_config.unlink()
        print("已移除打包目录中的 config.json，避免带上本机密钥。")
    print(f"完成：{DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
