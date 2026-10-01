"""截图说明文件、插入顺序和删除。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.screenshots import (
    NoteFrame,
    Screenshot,
    ScreenshotDocument,
    ScreenshotNote,
    append_screenshot_title_tags,
    default_note_slots,
    interleave_screenshots,
    load_screenshots,
    note_ordinal_labels,
    remove_screenshot,
    save_screenshots,
    scan_screenshots_under,
    screenshot_dir,
    screenshot_export_filename,
    screenshot_image_path,
    suggested_screenshot_title,
    tags_in_title,
    title_has_known_tag,
)
from core.subtitle import SubtitleSegment
from core.subtitle_tags import TAG_CATEGORIES, _general_tags


def _segment(index: int, start: float) -> SubtitleSegment:
    return SubtitleSegment(index, start, start + 2, f"第{index}句")


def _shot(shot_id: str, seconds: float, created_at: int = 1) -> Screenshot:
    return Screenshot(
        id=shot_id,
        title="画面",
        time=seconds,
        frame=12,
        image=f"{shot_id}.png",
        created_at=created_at,
        updated_at=created_at,
        notes=[
            ScreenshotNote("aabbccddeeff", "记下", created_at, created_at),
        ],
    )


class ScreenshotStoreTests(unittest.TestCase):
    def test_one_document_lives_in_the_video_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "电脑.mp4"
            media.write_bytes(b"video")
            document = ScreenshotDocument(entries=[_shot("a1b2c3d4e5f6", 12.4, 20)])
            save_screenshots(media, document)
            stored = load_screenshots(media)
            self.assertEqual(screenshot_dir(media).name, "screenshot")
            self.assertEqual(screenshot_dir(media).parent.name, "电脑.mp4.data")
            self.assertEqual(len(list(screenshot_dir(media).glob("*.json"))), 1)
            self.assertEqual(stored.entries[0].title, "画面")
            self.assertEqual(stored.entries[0].frame, 12)
            self.assertEqual(stored.entries[0].notes[0].text, "记下")
            self.assertIsNone(stored.entries[0].notes[0].frame)
            self.assertEqual(
                screenshot_image_path(media, stored.entries[0].id).name,
                "a1b2c3d4e5f6.png",
            )

    def test_equal_time_screenshot_comes_after_that_subtitle(self) -> None:
        segments = [_segment(1, 10), _segment(2, 15)]
        shots = [
            _shot("aaaaaaaaaaaa", 5, 1),
            _shot("bbbbbbbbbbbb", 10, 2),
            _shot("cccccccccccc", 12.4, 3),
            _shot("dddddddddddd", 15, 4),
            _shot("eeeeeeeeeeee", 20, 5),
        ]
        rows = interleave_screenshots(segments, shots)
        self.assertEqual(
            [(row.kind, row.segment_index, row.screenshot_id) for row in rows],
            [
                ("screenshot", -1, "aaaaaaaaaaaa"),
                ("subtitle", 0, ""),
                ("screenshot", -1, "bbbbbbbbbbbb"),
                ("screenshot", -1, "cccccccccccc"),
                ("subtitle", 1, ""),
                ("screenshot", -1, "dddddddddddd"),
                ("screenshot", -1, "eeeeeeeeeeee"),
            ],
        )

    def test_same_moment_screenshots_follow_creation_order(self) -> None:
        rows = interleave_screenshots(
            [_segment(1, 8)],
            [
                _shot("bbbbbbbbbbbb", 8, 30),
                _shot("aaaaaaaaaaaa", 8, 10),
            ],
        )
        self.assertEqual(
            [row.screenshot_id for row in rows if row.kind == "screenshot"],
            ["aaaaaaaaaaaa", "bbbbbbbbbbbb"],
        )

    def test_deleting_a_note_and_the_last_screenshot_clears_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "电脑.mp4"
            media.write_bytes(b"video")
            shot = _shot("a1b2c3d4e5f6", 3, 10)
            shot.notes.append(ScreenshotNote("b1b2c3d4e5f6", "第二条", 11, 11))
            save_screenshots(media, ScreenshotDocument(entries=[shot]))
            image = screenshot_image_path(media, shot.id)
            image.write_bytes(b"png")
            loaded = load_screenshots(media)
            loaded.entries[0].notes = [loaded.entries[0].notes[0]]
            save_screenshots(media, loaded)
            self.assertEqual(len(load_screenshots(media).entries[0].notes), 1)
            remove_screenshot(media, loaded, shot.id)
            self.assertFalse(screenshot_dir(media).exists())
            self.assertFalse(image.exists())

    def test_one_note_starts_in_the_middle_and_at_most_half_wide(self) -> None:
        x, y, width, height = default_note_slots(1)[0]
        self.assertAlmostEqual(width, 0.5)
        self.assertAlmostEqual(x + width / 2, 0.5)
        self.assertAlmostEqual(y + height / 2, 0.5)
        self.assertLessEqual(width, 0.5)

    def test_several_notes_spread_across_the_picture(self) -> None:
        slots = default_note_slots(3)
        self.assertEqual(len(slots), 3)
        previous_right = 0.0
        for x, _y, width, _height in slots:
            self.assertLessEqual(width, 1 / 3 + 1e-9)
            self.assertGreaterEqual(x, previous_right - 1e-9)
            self.assertLessEqual(x + width, 1 + 1e-9)
            previous_right = x + width

    def test_note_box_is_saved_with_that_note(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "电脑.mp4"
            media.write_bytes(b"video")
            shot = _shot("a1b2c3d4e5f6", 4, 10)
            shot.notes[0].frame = NoteFrame(
                x=0.2,
                y=0.3,
                width=0.5,
                height=0.25,
                background="#1565C0",
                opacity=0.4,
                font=0.08,
                color="#FFFFFF",
                align="left",
            )
            save_screenshots(media, ScreenshotDocument(entries=[shot]))
            frame = load_screenshots(media).entries[0].notes[0].frame
            self.assertIsNotNone(frame)
            assert frame is not None
            self.assertAlmostEqual(frame.x, 0.2)
            self.assertAlmostEqual(frame.y, 0.3)
            self.assertAlmostEqual(frame.width, 0.5)
            self.assertAlmostEqual(frame.height, 0.25)
            self.assertEqual(frame.background, "#1565C0")
            self.assertAlmostEqual(frame.opacity, 0.4)
            self.assertAlmostEqual(frame.font, 0.08)
            self.assertLessEqual(frame.font, 1)
            raw = (screenshot_dir(media) / "screenshots.json").read_text(encoding="utf-8")
            self.assertIn('"font": 0.08', raw)
            self.assertNotIn("font_size", raw)
            self.assertEqual(frame.color, "#FFFFFF")
            self.assertEqual(frame.align, "left")

    def test_blank_note_text_is_not_kept(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "课.mp4"
            media.write_bytes(b"video")
            shot = _shot("a1b2c3d4e5f6", 1, 1)
            shot.notes.append(ScreenshotNote("b1b2c3d4e5f6", "   ", 2, 2))
            save_screenshots(media, ScreenshotDocument(entries=[shot]))
            loaded = load_screenshots(media)
            self.assertEqual([note.text for note in loaded.entries[0].notes], ["记下"])
            loaded.entries[0].frame = None
            save_screenshots(media, loaded)
            self.assertIsNone(load_screenshots(media).entries[0].frame)

    def test_export_filename_uses_zm_prefix_and_title(self) -> None:
        self.assertEqual(screenshot_export_filename("第一张"), "zm-第一张.jpg")
        self.assertEqual(screenshot_export_filename("c/d"), "zm-c_d.jpg")
        self.assertEqual(screenshot_export_filename(""), "zm-截图.jpg")
        self.assertEqual(screenshot_export_filename("  "), "zm-截图.jpg")

    def test_scan_screenshots_under_uses_video_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "课程.mp4"
            nested_dir = root / "子目录"
            nested_dir.mkdir()
            nested_media = nested_dir / "练习.mp4"
            media.write_bytes(b"video")
            nested_media.write_bytes(b"video")
            (root / "zm-无关.jpg").write_bytes(b"jpg")

            save_screenshots(
                media,
                ScreenshotDocument(
                    entries=[
                        Screenshot(
                            id="aaaaaaaaaaaa",
                            title="第一张",
                            time=1.0,
                            frame=1,
                            image="aaaaaaaaaaaa.png",
                            created_at=100,
                            updated_at=200,
                            notes=[],
                        )
                    ]
                ),
            )
            image = screenshot_image_path(media, "aaaaaaaaaaaa")
            image.write_bytes(b"png")

            save_screenshots(
                nested_media,
                ScreenshotDocument(
                    entries=[
                        Screenshot(
                            id="bbbbbbbbbbbb",
                            title="笔记图",
                            time=2.0,
                            frame=2,
                            image="bbbbbbbbbbbb.png",
                            created_at=300,
                            updated_at=400,
                            notes=[ScreenshotNote("cccccccccccc", "一条笔记", 300, 400)],
                        )
                    ]
                ),
            )
            nested_image = screenshot_image_path(nested_media, "bbbbbbbbbbbb")
            nested_image.write_bytes(b"png")

            # 只有 json、没有对应视频的不算
            orphan_bundle = root / "失踪.mp4.data" / "screenshot"
            orphan_bundle.mkdir(parents=True)
            (orphan_bundle / "screenshots.json").write_text(
                '{"version":1,"screenshots":[]}',
                encoding="utf-8",
            )

            found = scan_screenshots_under(root)
            self.assertEqual(
                [
                    (
                        item.title,
                        item.relative_path,
                        item.created_at,
                        item.modified_at,
                        item.note_count,
                    )
                    for item in found
                ],
                [
                    ("第一张", f"课程.mp4.data/screenshot/{image.name}", 100, 200, 0),
                    ("笔记图", f"子目录/练习.mp4.data/screenshot/{nested_image.name}", 300, 400, 1),
                ],
            )

            from core.screenshots import paginate_screenshots_by_path

            path_page, path_index, path_pages = paginate_screenshots_by_path(found, 0)
            self.assertEqual(path_pages, 2)
            self.assertEqual(path_index, 0)
            self.assertEqual([item.title for item in path_page], ["第一张"])
            self.assertEqual(
                [item.title for item in paginate_screenshots_by_path(found, 1)[0]],
                ["笔记图"],
            )

            # 时间缺失时用图片保存时间补全
            bad_media = root / "补全.mp4"
            bad_media.write_bytes(b"video")
            save_screenshots(
                bad_media,
                ScreenshotDocument(
                    entries=[
                        Screenshot(
                            id="dddddddddddd",
                            title="补时间",
                            time=3.0,
                            frame=3,
                            image="dddddddddddd.png",
                            created_at=0,
                            updated_at=0,
                            notes=[],
                        )
                    ]
                ),
            )
            bad_image = screenshot_image_path(bad_media, "dddddddddddd")
            bad_image.write_bytes(b"png")
            stamped = next(item for item in scan_screenshots_under(root) if item.shot_id == "dddddddddddd")
            self.assertGreater(stamped.created_at, 0)
            self.assertGreater(stamped.modified_at, 0)

            # 广度优先：同层先于更深一层；同层按视频名排序
            deep_dir = nested_dir / "更深层"
            deep_dir.mkdir()
            deep_media = deep_dir / "深层.mp4"
            deep_media.write_bytes(b"video")
            save_screenshots(
                deep_media,
                ScreenshotDocument(
                    entries=[
                        Screenshot(
                            id="eeeeeeeeeeee",
                            title="深层图",
                            time=4.0,
                            frame=4,
                            image="eeeeeeeeeeee.png",
                            created_at=500,
                            updated_at=600,
                            notes=[],
                        )
                    ]
                ),
            )
            screenshot_image_path(deep_media, "eeeeeeeeeeee").write_bytes(b"png")
            ordered = [item.title for item in scan_screenshots_under(root)]
            # 根目录内按视频文件名排序：补全.mp4 在 课程.mp4 前
            self.assertEqual(ordered[:3], ["补时间", "第一张", "笔记图"])
            self.assertIn("深层图", ordered)
            self.assertLess(ordered.index("笔记图"), ordered.index("深层图"))

    def test_suggested_title_uses_filename_and_tags(self) -> None:
        self.assertEqual(suggested_screenshot_title("考试介绍", ["真题"]), "考试介绍-真题")
        self.assertEqual(
            suggested_screenshot_title("考试介绍", ["真题", "待复习"]),
            "考试介绍-待复习-真题",
        )
        self.assertEqual(suggested_screenshot_title("考试介绍", []), "考试介绍")
        self.assertEqual(suggested_screenshot_title("  ", []), "视频")

    def test_chosen_tags_are_appended_with_hyphens(self) -> None:
        self.assertEqual(append_screenshot_title_tags("考试介绍-真题", ["重点", "易错"]), "考试介绍-真题-重点-易错")
        self.assertEqual(append_screenshot_title_tags("", ["真题", "重点"]), "重点-真题")
        self.assertEqual(append_screenshot_title_tags("考试介绍-", ["真题"]), "考试介绍-真题")

    def test_title_without_a_known_tag_is_detected(self) -> None:
        self.assertFalse(title_has_known_tag("考试介绍"))
        self.assertTrue(title_has_known_tag("考试介绍-真题"))
        self.assertTrue(title_has_known_tag("考试介绍-还没想好"))
        self.assertTrue(title_has_known_tag("考试介绍-课堂", ["课堂"]))
        self.assertFalse(title_has_known_tag("考试介绍-课堂"))
        self.assertEqual(tags_in_title("考试介绍-真题-重点"), ["真题", "重点"])
        self.assertEqual(tags_in_title("考试介绍-课堂", ["课堂"]), ["课堂"])

    def test_undecided_tag_stays_last_in_general_category(self) -> None:
        general = dict(TAG_CATEGORIES)["通用"]
        self.assertEqual(general[-1], "还没想好")
        self.assertLess(general.index("存疑"), general.index("还没想好"))
        self.assertEqual(_general_tags("重点", "还没想好", "新标签")[-1], "还没想好")

    def test_note_labels_follow_creation_order(self) -> None:
        older = ScreenshotNote("aaaaaaaaaaaa", "先", 100, 500)
        newer = ScreenshotNote("bbbbbbbbbbbb", "后", 200, 200)
        alone = ScreenshotNote("cccccccccccc", "单", 300, 300)
        self.assertEqual(note_ordinal_labels([alone]), {})
        self.assertEqual(
            note_ordinal_labels([newer, older]),
            {"aaaaaaaaaaaa": "Note 1", "bbbbbbbbbbbb": "Note 2"},
        )

    def test_reused_style_keeps_position(self) -> None:
        from gui.note_overlay import apply_reused_frame_style

        target = NoteFrame(x=0.1, y=0.2, width=0.3, height=0.25, opacity=0.5, font=0.04, color="#FFFFFF", background="#000000")
        source = NoteFrame(x=0.5, y=0.6, width=0.4, height=0.35, opacity=0.9, font=0.08, color="#E53935", background="#FFFFFF", align="left")
        apply_reused_frame_style(target, source)
        self.assertEqual(target.x, 0.1)
        self.assertEqual(target.y, 0.2)
        self.assertEqual(target.width, 0.4)
        self.assertEqual(target.height, 0.35)
        self.assertEqual(target.opacity, 0.9)
        self.assertEqual(target.font, 0.08)
        self.assertEqual(target.color, "#E53935")
        self.assertEqual(target.background, "#FFFFFF")
        self.assertEqual(target.align, "left")


class ScreenshotCompositeTests(unittest.TestCase):
    def test_visible_notes_are_painted_on_composite(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtGui import QColor, QPixmap
        from PyQt6.QtWidgets import QApplication

        from gui.screenshot_viewer import ScreenshotViewerWindow

        if QApplication.instance() is None:
            ScreenshotCompositeTests._app = QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "shot.png"
            pixmap = QPixmap(200, 100)
            pixmap.fill(QColor("#FF0000"))
            self.assertTrue(pixmap.save(str(image_path), "PNG"))
            left = ScreenshotNote(
                "aaaaaaaaaaaa",
                "左",
                1_700_000_000_000,
                1_700_000_000_000,
                NoteFrame(x=0.0, y=0.2, width=0.4, height=0.6, opacity=1.0, background="#FFFFFF", font=0.04),
            )
            right = ScreenshotNote(
                "bbbbbbbbbbbb",
                "右",
                1_700_000_000_000,
                1_700_000_000_000,
                NoteFrame(x=0.6, y=0.2, width=0.4, height=0.6, opacity=1.0, background="#FFFFFF", font=0.04),
            )
            shot = Screenshot("c1c1c1c1c1c1", "标题-真题", 1.0, 1, "c1c1c1c1c1c1.png", 1_700_000_000_000, 1_700_000_000_000, [left, right])
            viewer = ScreenshotViewerWindow()
            viewer.resize(640, 360)
            viewer.show_shots(
                [shot],
                [image_path],
                0,
                opacity=1,
                font_size=0.04,
                color="#1A1A1A",
                align="center",
                background="#FFFFFF",
            )
            both = viewer.render_composite()
            self.assertIsNotNone(both)
            assert both is not None
            self.assertTrue(self._is_white(both.pixelColor(40, 50)))
            self.assertTrue(self._is_white(both.pixelColor(160, 50)))
            self.assertEqual(viewer.current_title(), "标题-真题")
            viewer.close()

    @staticmethod
    def _is_white(color) -> bool:
        return color.red() > 240 and color.green() > 240 and color.blue() > 240

    @staticmethod
    def _is_red(color) -> bool:
        return color.red() > 240 and color.green() < 20 and color.blue() < 20
