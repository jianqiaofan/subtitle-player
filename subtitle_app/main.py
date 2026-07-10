import sys
from pathlib import Path

try:
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
        raise SystemExit(1) from exc
    raise

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("字幕播放器")

    if "--transcribe" in sys.argv:
        from gui.main_window import TranscribeWindow

        window = TranscribeWindow()
    else:
        from gui.player_window import PlayerWindow

        window = PlayerWindow()

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
