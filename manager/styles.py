"""Shared, dependency-light Qt styling for DSH Harness Companion."""

from __future__ import annotations

import sys
from pathlib import Path

ACCENT = "#22D3C5"
ACCENT_HOVER = "#5EEAD4"
BACKGROUND = "#0B1220"
SURFACE = "#111C30"
TEXT = "#E7EEF8"
MUTED = "#94A3B8"
ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def app_stylesheet() -> str:
    """Return the application-wide QSS theme used by the native interface."""
    return r"""
    QWidget {
        color: #E7EEF8;
        font-family: "Microsoft YaHei UI";
        font-size: 11pt;
    }
    QWidget#mainWindow, QDialog {
        background-color: #0B1220;
    }
    QWidget#appShell, QWidget#pageSurface, QStackedWidget {
        background-color: #0B1220;
    }
    QWidget#sidebar {
        background-color: #101A2B;
        border-right: 1px solid #243249;
    }
    QListWidget#navList {
        background-color: transparent;
        border: 0;
        outline: 0;
        padding: 4px 2px;
    }
    QListWidget#navList::item {
        color: #A9B8CB;
        border-radius: 9px;
        padding: 10px 12px;
        margin: 2px 0;
    }
    QListWidget#navList::item:hover {
        background-color: #1A2940;
        color: #F1F5F9;
    }
    QListWidget#navList::item:selected {
        background-color: #123D48;
        color: #8AF0E3;
        border: 1px solid #1D6871;
        font-weight: 700;
    }
    QListWidget#navList::item:selected:active {
        background-color: #164E63;
    }
    QLabel#sidebarTitle {
        color: #F8FAFC;
        font-size: 14.25pt;
        font-weight: 700;
        padding: 8px 4px 18px 4px;
    }
    QLabel#sidebarSubtitle, QLabel#pageSubtitle { color: #A9BBCD; font-size: 10.5pt; }
    QLabel#pageTitle {
        color: #F8FAFC;
        font-size: 19.5pt;
        font-weight: 700;
        padding: 2px 0 8px 0;
    }
    QLabel#heroStatus {
        color: #F8FAFC;
        font-size: 14.25pt;
        font-weight: 700;
        padding: 2px 0;
    }
    QLabel#homeHeroTitle {
        color: #F8FAFC;
        font-size: 23pt;
        font-weight: 750;
    }
    QLabel#homeHeroDetail {
        color: #B8CBDD;
        font-size: 12pt;
    }
    QFrame#homeSummary {
        background-color: #14283E;
        border: 1px solid #36536D;
        border-radius: 15px;
    }
    QFrame#homeDivider { color: #35516A; }
    QLabel#homeRowLabel {
        color: #AFC5D8;
        font-size: 11.5pt;
        font-weight: 600;
    }
    QLabel#homeRowValue {
        color: #EDF8FB;
        font-size: 12pt;
        font-weight: 650;
    }
    QComboBox#homeModelSelect {
        font-size: 12pt;
        font-weight: 650;
        padding: 10px 14px;
    }
    QLabel#homeHint {
        color: #F1D292;
        font-size: 11.5pt;
    }
    QLabel#homeNote {
        color: #93B4C6;
        font-size: 10.5pt;
    }
    QPushButton[homeAction="true"] {
        font-size: 12pt;
        font-weight: 700;
        padding: 13px 18px;
    }
    QPushButton#homeSetupButton {
        background-color: transparent;
        border: 1px solid #4D8393;
        color: #9EEDE3;
        font-size: 11pt;
        padding: 11px 19px;
    }
    QLabel#cardTitle {
        color: #DCE8F5;
        font-size: 12pt;
        font-weight: 700;
    }
    QLabel#sectionTitle {
        color: #DCE8F5;
        font-size: 12pt;
        font-weight: 700;
        padding: 0 0 5px 0;
    }
    QLabel#sectionHint { color: #94A3B8; font-size: 10.5pt; }
    QWidget#card, QFrame#card {
        background-color: #111C30;
        border: 1px solid #26364E;
        border-radius: 16px;
    }
    QFrame#subsection {
        background-color: #142238;
        border: 1px solid #2A3C55;
        border-radius: 12px;
        padding: 10px;
    }
    QWidget#heroCard, QFrame#heroCard {
        background-color: #13243A;
        border: 1px solid #285064;
        border-radius: 18px;
    }
    QLabel#statusBadge {
        background-color: #1B2B40;
        color: #CBD5E1;
        border: 1px solid #344760;
        border-radius: 12px;
        padding: 5px 11px;
        font-weight: 600;
    }
    QLabel#statusBadge[state="RUNNING"], QLabel#statusBadge[state="READY"] {
        background-color: #12352F;
        color: #8AF0D7;
        border-color: #1F806F;
    }
    QLabel#statusBadge[state="LOADING"] {
        background-color: #3A2C17;
        color: #F5D28A;
        border-color: #846221;
    }
    QLabel#statusBadge[state="ERROR"] {
        background-color: #3D2027;
        color: #FFB4BE;
        border-color: #8D3D4A;
    }
    QPushButton {
        background-color: #1A2940;
        color: #E7EEF8;
        border: 1px solid #344760;
        border-radius: 10px;
        padding: 9px 16px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #355576;
        border: 2px solid #69B6CE;
    }
    QPushButton:pressed {
        background-color: #0D7490;
        color: #FFFFFF;
        border: 2px solid #B2FFF0;
        padding-top: 11px;
        padding-bottom: 7px;
    }
    QPushButton:focus {
        border: 2px solid #8DE6D8;
    }
    QPushButton[busy="true"] {
        background-color: #25506A;
        color: #F8FFFE;
        border: 2px solid #4EDAC7;
    }
    QPushButton[feedbackResult="success"] {
        background-color: #17634F;
        color: #F0FFFB;
        border: 2px solid #72F2CA;
    }
    QPushButton[feedbackResult="error"] {
        background-color: #6F2D3A;
        color: #FFF5F4;
        border: 2px solid #FF9BA8;
    }
    QPushButton:disabled {
        background-color: #182233;
        color: #64748B;
        border-color: #253247;
    }
    QPushButton#primaryButton {
        background-color: #0F766E;
        color: #F0FDFA;
        border: 1px solid #2DD4BF;
    }
    QPushButton#primaryButton:hover { background-color: #0DAE9E; border: 2px solid #B5FFF0; }
    QPushButton#primaryButton:pressed { background-color: #07544E; border: 2px solid #FFFFFF; }
    QPushButton#primaryButton:disabled {
        background-color: #173A3C;
        color: #91AAA9;
        border-color: #315B5D;
    }
    QPushButton#dangerButton {
        background-color: #7F2937;
        color: #FFF1F2;
        border: 1px solid #FB7185;
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 9.75pt;
    }
    QPushButton#dangerButton:hover { background-color: #9F3344; }
    QPushButton#dangerButton:pressed { background-color: #60202C; border: 2px solid #FFD1D7; }
    QPushButton#dangerButton:disabled {
        background-color: #38272D;
        color: #AA858B;
        border-color: #63414A;
    }
    QPushButton#secondaryButton {
        background-color: transparent;
        border-color: #344760;
    }
    QPushButton#secondaryButton:hover, QPushButton#homeSetupButton:hover {
        background-color: #355576; color: #FFFFFF; border: 2px solid #69B6CE;
    }
    QPushButton#secondaryButton:pressed, QPushButton#homeSetupButton:pressed {
        background-color: #0D7490; color: #FFFFFF; border: 2px solid #B2FFF0;
    }
    QPushButton#secondaryButton:disabled, QPushButton#homeSetupButton:disabled {
        background-color: #182233; color: #71849A; border: 1px solid #334052;
    }
    QPushButton#primaryButton:focus, QPushButton#secondaryButton:focus,
    QPushButton#dangerButton:focus, QPushButton#homeSetupButton:focus {
        border: 2px solid #8DE6D8;
    }
    QPushButton#primaryButton[busy="true"], QPushButton#secondaryButton[busy="true"],
    QPushButton#dangerButton[busy="true"] {
        background-color: #25506A; color: #F8FFFE; border: 2px solid #4EDAC7;
    }
    QPushButton#primaryButton[feedbackResult="success"],
    QPushButton#secondaryButton[feedbackResult="success"],
    QPushButton#dangerButton[feedbackResult="success"] {
        background-color: #17634F; color: #F0FFFB; border: 2px solid #72F2CA;
    }
    QPushButton#primaryButton[feedbackResult="error"],
    QPushButton#secondaryButton[feedbackResult="error"],
    QPushButton#dangerButton[feedbackResult="error"] {
        background-color: #6F2D3A; color: #FFF5F4; border: 2px solid #FF9BA8;
    }
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #0D1728;
        color: #E7EEF8;
        border: 1px solid #344760;
        border-radius: 9px;
        padding: 9px 11px;
        selection-background-color: #0F766E;
        min-height: 20px;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
        border: 1px solid #22D3C5;
    }
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
        background-color: #131D2D;
        color: #7B8DA1;
        border-color: #29394E;
    }
    QComboBox::drop-down {
        border: 0;
        width: 28px;
    }
    QComboBox::down-arrow {
        image: url(@CHEVRON_URL@);
        width: 13px;
        height: 13px;
    }
    QComboBox QAbstractItemView {
        background-color: #111C30;
        color: #E7EEF8;
        border: 1px solid #344760;
        selection-background-color: #164E63;
        outline: 0;
    }
    QTabWidget::pane {
        background-color: #0B1220;
        border: 1px solid #26364E;
        border-radius: 12px;
        top: -1px;
    }
    QTabBar::tab {
        background-color: #111C30;
        color: #A9B8CB;
        border: 1px solid #26364E;
        border-bottom: 0;
        border-top-left-radius: 9px;
        border-top-right-radius: 9px;
        padding: 9px 16px;
        margin-right: 4px;
    }
    QTabBar::tab:selected {
        background-color: #172840;
        color: #75E9DB;
        border-color: #2C6470;
    }
    QScrollArea { border: 0; background: #0B1220; }
    QTableWidget {
        background-color: #111C30;
        alternate-background-color: #172840;
        color: #E7EEF8;
        gridline-color: #344760;
        border: 1px solid #344760;
        border-radius: 7px;
        selection-background-color: #164E63;
        alternate-background-color: #15243A;
    }
    QTreeWidget {
        background-color: #111C30;
        alternate-background-color: #15243A;
        color: #E7EEF8;
        border: 1px solid #344760;
        border-radius: 7px;
        selection-background-color: #164E63;
    }
    QTreeWidget::item { min-height: 40px; padding: 4px 6px; }
    QTreeWidget::item:selected { background-color: #164E63; color: #F8FAFC; }
    QListWidget {
        background-color: #0D1728;
        color: #E7EEF8;
        border: 1px solid #344760;
        border-radius: 9px;
        outline: 0;
    }
    QListWidget::item { padding: 7px 9px; border-radius: 7px; }
    QListWidget::item:selected { background-color: #164E63; color: #F8FAFC; }
    QHeaderView::section {
        background-color: #1B2B40;
        color: #CBD5E1;
        padding: 5px;
        border: 0;
        border-right: 1px solid #344760;
    }
    QScrollBar:vertical {
        background: transparent;
        width: 12px;
        margin: 4px 2px;
    }
    QScrollBar::handle:vertical {
        background: #344760;
        min-height: 28px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover { background: #4A6685; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; }
    QCheckBox { spacing: 9px; }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
        border: 1px solid #52647C;
        border-radius: 5px;
        background: #0D1728;
    }
    QCheckBox::indicator:checked {
        background: #0F766E;
        border-color: #2DD4BF;
        image: url(@CHECKMARK_URL@);
    }
    QSlider::groove:horizontal {
        height: 6px;
        background: #26364E;
        border-radius: 3px;
    }
    QSlider::sub-page:horizontal { background: #22D3C5; border-radius: 3px; }
    QSlider::handle:horizontal {
        background: #99F6E4;
        border: 2px solid #0F766E;
        width: 16px;
        margin: -7px 0;
        border-radius: 9px;
    }
    QToolTip {
        background-color: #172840;
        color: #F8FAFC;
        border: 1px solid #3A526F;
        padding: 6px 8px;
        border-radius: 6px;
    }
    QWidget#bubbleUser, QFrame#bubbleUser {
        background-color: #123D48;
        border: 1px solid #1D6871;
        border-radius: 14px;
        padding: 12px;
    }
    QWidget#bubbleAssistant, QFrame#bubbleAssistant {
        background-color: #142238;
        border: 1px solid #2C405B;
        border-radius: 14px;
        padding: 12px;
    }
    QWidget#bubbleThinking, QFrame#bubbleThinking {
        background-color: #191F31;
        border: 1px solid #393D5A;
        border-radius: 12px;
        padding: 10px;
        color: #B9B8D8;
    }
    QLabel#mutedText { color: #94A3B8; }
    QLabel#metricValue { color: #F8FAFC; font-size: 15pt; font-weight: 700; }
    QMenu {
        background-color: #111C30;
        color: #E7EEF8;
        border: 1px solid #344760;
        padding: 5px;
    }
    QMenu::item { padding: 7px 24px 7px 10px; border-radius: 5px; }
    QMenu::item:selected { background-color: #164E63; }
    """.replace("@CHECKMARK_URL@", _asset_url("checkmark.svg")).replace(
        "@CHEVRON_URL@", _asset_url("chevron-down.svg"))


def _checkmark_url() -> str:
    return _asset_url("checkmark.svg")


def _asset_url(name: str) -> str:
    from PySide6.QtCore import QUrl

    path = next((item for item in _asset_directories() if (item / name).is_file()), None)
    if path is None:
        return ""
    path = path / name
    return QUrl.fromLocalFile(str(path)).toString()


def _asset_directories() -> tuple[Path, ...]:
    """Return source and PyInstaller one-file asset locations, in preference order."""
    manager_dir = Path(__file__).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", manager_dir.parent))
    return (manager_dir / "assets", bundle_root / "manager" / "assets", bundle_root / "assets")


def app_icon(state: str | None = None):
    """Load the shared app icon; optionally add a small runtime status marker.

    ``state`` accepts RUNNING, READY, LOADING, or ERROR. The optional argument
    keeps the original no-argument window/tray API fully compatible.
    """
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    asset_dir = next((item for item in _asset_directories() if (item / "companion-icon.png").is_file()), None)
    icon = QIcon()
    if asset_dir is not None:
        for size in ICON_SIZES:
            image_path = asset_dir / f"companion-icon-{size}.png"
            if image_path.is_file():
                icon.addFile(str(image_path), QSize(size, size))
        if icon.isNull():
            icon.addFile(str(asset_dir / "companion-icon.png"))
    else:
        # Fallback maintains a useful icon if packaging omitted the image files.
        fallback = QPixmap(64, 64)
        fallback.fill(Qt.GlobalColor.transparent)
        icon = QIcon(fallback)

    state_color = {
        "RUNNING": "#4ADE80",
        "READY": "#38D9B7",
        "LOADING": "#F4C66B",
        "ERROR": "#FB7185",
    }.get((state or "").upper())
    if state_color:
        pixmap = icon.pixmap(64, 64)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#0C1728"), 3))
        painter.setBrush(QColor(state_color))
        painter.drawEllipse(48, 48, 13, 13)
        painter.setPen(QPen(QColor("#F1FFF9"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(50, 50, 9, 9)
        painter.end()
        return QIcon(pixmap)
    return icon
