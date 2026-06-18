import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

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
