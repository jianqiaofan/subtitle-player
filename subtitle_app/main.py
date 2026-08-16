import os
import sys
from pathlib import Path

# Must be set before importing QtMultimedia: FFmpeg + HW decode (D3D11VA on Windows).
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console_window import install_console_host, reveal_console_and_wait

_CONSOLE_TITLE = "转写工具 - 后台" if "--transcribe" in sys.argv else "字幕播放器 - 后台"
install_console_host(title=_CONSOLE_TITLE)

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    if exc.name == "PyQt6":
        print(
            "未找到 PyQt6。升级 Python 后需要为当前版本重新安装依赖。\n"
            "请在本目录运行「安装依赖.bat」，或执行：\n"
            "  py -3 -m pip install -r requirements.txt\n"
            f"当前解释器：{sys.executable} ({sys.version.split()[0]})",
            file=sys.stderr,
        )
        reveal_console_and_wait()
        raise SystemExit(1) from exc
    reveal_console_and_wait()
    raise


def main() -> int:
    # Prefer exact device-pixel sizing to reduce soft scaling of video frames.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("字幕播放器")

    if "--transcribe" in sys.argv:
        from gui.main_window import TranscribeWindow

        files = [Path(arg) for arg in sys.argv[1:] if not arg.startswith("-") and Path(arg).is_file()]
        window = TranscribeWindow(initial_files=files or None)
    else:
        from gui.player_window import PlayerWindow

        window = PlayerWindow()

    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback

        traceback.print_exc()
        reveal_console_and_wait()
        raise SystemExit(1)
