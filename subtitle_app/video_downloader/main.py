"""
视频下载器 — 启动入口

用法:
  1. pip install -r requirements.txt
  2. python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
for path in (ROOT, PARENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.console_window import install_console_host, reveal_console_and_wait

install_console_host(title="视频下载器 - 后台")


def main() -> None:
    from app.ui.main_window import MainWindow

    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback

        traceback.print_exc()
        reveal_console_and_wait()
        raise SystemExit(1)
