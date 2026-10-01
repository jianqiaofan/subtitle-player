"""本机 tip「下回不再提醒」持久化。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.config import AppConfig, load_config
from gui.tip_dialog import TIP_SCREENSHOT_EXPORT, dismiss_tip, tip_is_dismissed


class TipDismissTests(unittest.TestCase):
    def test_dismiss_tip_appends_once(self) -> None:
        config = AppConfig()
        dismiss_tip(config, TIP_SCREENSHOT_EXPORT, persist=False)
        dismiss_tip(config, TIP_SCREENSHOT_EXPORT, persist=False)
        self.assertEqual(config.dismissed_tips, [TIP_SCREENSHOT_EXPORT])
        self.assertTrue(tip_is_dismissed(config, TIP_SCREENSHOT_EXPORT))
        self.assertFalse(tip_is_dismissed(config, "other_tip"))

    def test_load_config_normalizes_dismissed_tips(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "dismissed_tips": [
                            TIP_SCREENSHOT_EXPORT,
                            "",
                            TIP_SCREENSHOT_EXPORT,
                            "  another_tip  ",
                            12,
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch("core.config.CONFIG_PATH", config_path):
                cfg = load_config()
            self.assertEqual(
                cfg.dismissed_tips,
                [TIP_SCREENSHOT_EXPORT, "another_tip", "12"],
            )


if __name__ == "__main__":
    unittest.main()
