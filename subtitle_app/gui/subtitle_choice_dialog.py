from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from core.config import LIVE_SYNC_FILENAME_LABEL, load_config
from core.subtitle_resolve import (
    SubtitleAction,
    SubtitleChoice,
    find_subtitle_by_label,
    list_available_subtitles,
)
from core.sync_subtitle import SyncSubtitleStatus, assess_sync_subtitle


class SubtitleChoiceDialog(QDialog):
    def __init__(self, media_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.media_path = media_path.resolve()
        self._existing = list_available_subtitles(self.media_path)
        self._sync = assess_sync_subtitle(self.media_path, load_config())
        self._preferred_path = find_subtitle_by_label(self.media_path, LIVE_SYNC_FILENAME_LABEL)

        self.setWindowTitle("字幕来源")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(f"媒体文件：{self.media_path.name}\n请选择字幕来源：")
        )

        self.use_existing_radio = QRadioButton("使用已有字幕文件")
        self.resume_radio: QRadioButton | None = None
        if self._sync.status == SyncSubtitleStatus.INCOMPLETE:
            covered = self._sync.covered_until_sec
            minutes, secs = divmod(int(covered), 60)
            hours, minutes = divmod(minutes, 60)
            clock = f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
            self.resume_radio = QRadioButton(
                f"继续未完成的同步字幕（已覆盖至 {clock}）"
            )
        self.live_radio = QRadioButton("边播放边转（重新同步）")
        self.batch_radio = QRadioButton("全量转写（后台完成后再看）")

        self.existing_combo = QComboBox()
        for path, label in self._existing:
            self.existing_combo.addItem(f"{label} ({path.name})", str(path))

        existing_box = QGroupBox()
        existing_layout = QVBoxLayout(existing_box)
        existing_layout.addWidget(self.use_existing_radio)
        existing_layout.addWidget(self.existing_combo)
        layout.addWidget(existing_box)

        if self.resume_radio is not None:
            layout.addWidget(self.resume_radio)
        layout.addWidget(self.live_radio)
        layout.addWidget(self.batch_radio)

        self.force_retranscribe = QCheckBox("强制重新转录（忽略已有字幕文件）")
        layout.addWidget(self.force_retranscribe)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._configure_defaults()
        self.force_retranscribe.toggled.connect(self._on_force_toggled)

    def _configure_defaults(self) -> None:
        has_existing = bool(self._existing)
        self.use_existing_radio.setEnabled(has_existing)
        self.existing_combo.setEnabled(has_existing)

        if self._sync.status == SyncSubtitleStatus.COMPLETE and self._preferred_path is not None:
            self.use_existing_radio.setChecked(True)
            for index in range(self.existing_combo.count()):
                if self.existing_combo.itemData(index) == str(self._preferred_path):
                    self.existing_combo.setCurrentIndex(index)
                    break
        elif self._sync.status == SyncSubtitleStatus.INCOMPLETE and self.resume_radio is not None:
            self.resume_radio.setChecked(True)
        elif has_existing:
            self.use_existing_radio.setChecked(True)
        else:
            self.live_radio.setChecked(True)

    def _on_force_toggled(self, checked: bool) -> None:
        if checked:
            self.live_radio.setChecked(True)

    def get_choice(self) -> SubtitleChoice | None:
        if self.result() != QDialog.DialogCode.Accepted:
            return None

        if self.force_retranscribe.isChecked() or self.live_radio.isChecked():
            return SubtitleChoice(SubtitleAction.LIVE_TRANSCRIBE, label=LIVE_SYNC_FILENAME_LABEL)

        if self.resume_radio is not None and self.resume_radio.isChecked():
            return SubtitleChoice(
                SubtitleAction.RESUME_LIVE_TRANSCRIBE,
                subtitle_path=self._sync.subtitle_path,
                label=LIVE_SYNC_FILENAME_LABEL,
            )

        if self.batch_radio.isChecked():
            return SubtitleChoice(SubtitleAction.BATCH_TRANSCRIBE, label=LIVE_SYNC_FILENAME_LABEL)

        if self.use_existing_radio.isChecked():
            path_value = self.existing_combo.currentData()
            if not path_value:
                return SubtitleChoice(SubtitleAction.LIVE_TRANSCRIBE, label=LIVE_SYNC_FILENAME_LABEL)
            path = Path(path_value)
            label = self.existing_combo.currentText().split(" (", 1)[0]
            return SubtitleChoice(
                SubtitleAction.USE_EXISTING,
                subtitle_path=path,
                label=label,
            )

        return SubtitleChoice(SubtitleAction.LIVE_TRANSCRIBE, label=LIVE_SYNC_FILENAME_LABEL)

    @classmethod
    def ask(cls, media_path: Path, parent=None) -> SubtitleChoice | None:
        dialog = cls(media_path, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.get_choice()
