DARK_STYLE = """
QMainWindow, QWidget:not(QVideoWidget) {
    background-color: #393939;
    color: #ffffff;
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}
QVideoWidget {
    background-color: #000000;
    border: none;
}
QGroupBox {
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #e0e0e0;
}
QTableWidget {
    background-color: #2b2b2b;
    color: white;
    gridline-color: rgba(255, 255, 255, 0.15);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    selection-background-color: rgba(185, 128, 255, 0.25);
}
QHeaderView::section {
    background-color: #333333;
    color: white;
    padding: 6px;
    border: none;
    border-right: 1px solid rgba(255, 255, 255, 0.15);
    border-bottom: 1px solid rgba(255, 255, 255, 0.15);
}
QPushButton {
    color: #b980ff;
    background-color: #212121;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 7px 16px;
    min-height: 18px;
}
QPushButton:hover {
    border: 1px solid #b980ff;
    background-color: rgba(185, 128, 255, 0.08);
}
QPushButton:pressed {
    background-color: #1a1a1a;
}
QPushButton:disabled {
    color: #666666;
    border-color: #444444;
}
QPushButton#primaryButton {
    background-color: #6b3fa0;
    color: white;
    border: 1px solid #9060df;
    font-weight: bold;
}
QPushButton#primaryButton:hover {
    background-color: #7d4fbd;
}
QPushButton#iconButton {
    padding: 4px;
    min-width: 28px;
    max-width: 36px;
}
QPushButton#iconButton:checked {
    background-color: rgba(185, 128, 255, 0.18);
    border: 1px solid #b980ff;
}
QToolButton#toolbarMenuButton {
    color: #b980ff;
    background-color: #212121;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 6px 10px;
    min-height: 18px;
}
QToolButton#toolbarMenuButton:hover {
    border: 1px solid #b980ff;
    background-color: rgba(185, 128, 255, 0.08);
}
QToolButton#toolbarMenuButton:pressed {
    background-color: #1a1a1a;
}
QMenu {
    background-color: #2b2b2b;
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 0;
}
QMenu::item {
    padding: 8px 28px 8px 16px;
}
QMenu::item:selected {
    background-color: rgba(185, 128, 255, 0.28);
}
QMenu::item:disabled {
    color: #666666;
}
QMenu::separator {
    height: 1px;
    background-color: rgba(255, 255, 255, 0.12);
    margin: 4px 8px;
}
QComboBox, QLineEdit, QSpinBox {
    background-color: #2b2b2b;
    color: white;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 5px 8px;
    min-height: 20px;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #2b2b2b;
    color: white;
    selection-background-color: rgba(185, 128, 255, 0.35);
}
QPlainTextEdit {
    background-color: #2b2b2b;
    color: #dddddd;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    padding: 6px;
}
QProgressBar {
    background-color: #2b2b2b;
    border: 1px solid #555555;
    border-radius: 4px;
    text-align: center;
    color: white;
    height: 22px;
}
QProgressBar::chunk {
    background-color: #b980ff;
    border-radius: 3px;
}
QLabel#hintLabel {
    color: #aaaaaa;
    font-size: 12px;
}
QScrollBar:vertical {
    background: #393939;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #b980ff;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

PLAYER_LIST_STYLE = """
QListWidget {
    background-color: #2b2b2b;
    color: #eeeeee;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    padding: 4px;
    font-size: 13px;
}
QListWidget::item {
    padding: 8px 6px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
QListWidget::item:selected {
    background-color: rgba(185, 128, 255, 0.28);
    color: #ffffff;
}
QListWidget::item:hover {
    background-color: rgba(185, 128, 255, 0.12);
}
QSplitter::handle {
    background-color: rgba(255, 255, 255, 0.15);
    width: 4px;
}
"""
