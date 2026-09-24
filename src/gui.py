#!/usr/bin/env python3
"""Modern PySide6 interface for scanning and backing up Steam data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

try:
    from PySide6.QtCore import QSize, QThread, QTimer, Qt, QUrl, Signal
    from PySide6.QtGui import QCursor, QDesktopServices, QFont, QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLayout,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSizePolicy,
        QToolButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as error:  # pragma: no cover - depends on the desktop environment
    raise SystemExit("缺少 PySide6。请先运行：python -m pip install -r requirements.txt") from error

try:
    from src.backup_manager import (
        backup_metadata,
        backup_report,
        detect_report_changes,
        game_backup_directory,
        load_report,
    )
    from src.steam_save_scanner import build_report, discover_steam_root
    from src.history_manager import (
        HistoryPoint,
        clear_history_point,
        import_history_package,
        list_game_history,
        list_history,
        package_history,
        package_history_points,
        restore_game_history_point,
    )
except ModuleNotFoundError:  # Allows: python src/gui.py
    from backup_manager import (
        backup_metadata,
        backup_report,
        detect_report_changes,
        game_backup_directory,
        load_report,
    )
    from steam_save_scanner import build_report, discover_steam_root
    from history_manager import (
        HistoryPoint,
        clear_history_point,
        import_history_package,
        list_game_history,
        list_history,
        package_history,
        package_history_points,
        restore_game_history_point,
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "scan_result.generated.json"
MANIFEST_PATH = PROJECT_ROOT / "third_party" / "ludusavi-manifest" / "data" / "manifest.yaml"
BACKUP_ROOT = PROJECT_ROOT / "backup_result"
PINNED_GAMES_PATH = PROJECT_ROOT / "ui_settings.json"
PIN_OUTLINE_ICON = PROJECT_ROOT / "src" / "assets" / "pin-outline.svg"
PIN_FILLED_ICON = PROJECT_ROOT / "src" / "assets" / "pin-filled.svg"


STYLE_SHEET = """
QWidget {
    color: #202938;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QMainWindow, QDialog, QWidget#Root {
    background: #f4f6fa;
}
QLabel#PageTitle {
    color: #172033;
    font-size: 25px;
    font-weight: 700;
}
QLabel#PageSubtitle, QLabel#Muted, QLabel#StatusText, QLabel#CountText {
    color: #697386;
}
QFrame#Toolbar, QFrame#FilterBar, QFrame#HistoryRow {
    background: #ffffff;
    border: 1px solid #e4e8f0;
    border-radius: 12px;
}
QFrame#HistoryRow {
    border-radius: 10px;
}
QFrame#GameCard {
    background: #ffffff;
    border: 1px solid #e1e6ef;
    border-radius: 14px;
}
QFrame#GameCard:hover {
    border-color: #b9c7df;
}
QLabel#GameTitle {
    color: #182235;
    font-size: 17px;
    font-weight: 700;
}
QLabel#AppId {
    color: #778298;
    background: #f1f4f9;
    border-radius: 8px;
    padding: 4px 9px;
}
QLabel#SummaryBadge {
    color: #526078;
    background: #f5f7fb;
    border: 1px solid #e5e9f1;
    border-radius: 8px;
    padding: 4px 8px;
}
QLabel#ChangeBadge {
    border-radius: 8px;
    padding: 4px 8px;
    font-weight: 600;
}
QLabel#ChangeBadge[state="unchecked"] {
    color: #697386;
    background: #eef1f5;
    border: 1px solid #dfe4eb;
}
QLabel#ChangeBadge[state="clean"] {
    color: #237447;
    background: #e9f7ef;
    border: 1px solid #b9e2ca;
}
QLabel#ChangeBadge[state="changed"] {
    color: #8a5a00;
    background: #fff4d6;
    border: 1px solid #efd58a;
}
QLabel#LastBackup {
    color: #697386;
    font-size: 12px;
}
QLabel#LastBackup[active="true"] {
    color: #2368d8;
}
QLabel#LastBackup[active="true"]:hover {
    color: #124fae;
    text-decoration: underline;
}
QLabel#SectionLabel {
    color: #536078;
    font-weight: 600;
    padding-top: 2px;
}
QLabel#EmptyValue {
    color: #9aa3b2;
}
QLabel#PathLink {
    color: #2368d8;
    background: transparent;
    padding: 2px 0;
}
QLabel#PathLink:hover {
    color: #124fae;
    text-decoration: underline;
}
QLabel#PathLink[active="false"] {
    color: #9aa3b2;
}
QLabel#PathLink[active="false"]:hover {
    color: #9aa3b2;
    text-decoration: none;
}
QPushButton {
    min-height: 35px;
    padding: 0 15px;
    border: 1px solid #d7dde8;
    border-radius: 9px;
    background: #ffffff;
    color: #344054;
    font-weight: 600;
}
QPushButton:hover {
    background: #f7f9fc;
    border-color: #b9c3d2;
}
QPushButton:pressed {
    background: #edf1f7;
}
QPushButton:disabled {
    color: #a8b0bd;
    background: #f1f3f6;
    border-color: #e4e7ec;
}
QPushButton#PrimaryButton {
    color: #ffffff;
    background: #2767d7;
    border-color: #2767d7;
}
QPushButton#PrimaryButton:hover {
    background: #1f58bd;
    border-color: #1f58bd;
}
QPushButton#CardButton {
    min-height: 31px;
    padding: 0 12px;
}
QPushButton#DangerButton {
    color: #b42318;
    border-color: #efc0bb;
    background: #fffafa;
}
QPushButton#DangerButton:hover {
    color: #912018;
    border-color: #e59d96;
    background: #fff1f0;
}
QPushButton#WarningButton {
    color: #7a4d00;
    border-color: #e4bd68;
    background: #fff4cf;
}
QPushButton#WarningButton:hover {
    color: #653f00;
    border-color: #d5a849;
    background: #ffe9a3;
}
QPushButton#FilterChip {
    min-height: 29px;
    padding: 0 12px;
    border-radius: 15px;
    font-weight: 500;
}
QPushButton#FilterChip:checked {
    color: #1d58ba;
    background: #eaf2ff;
    border-color: #96b9f3;
}
QLineEdit {
    min-height: 33px;
    padding: 0 12px;
    color: #29364b;
    background: #f8f9fc;
    border: 1px solid #dce2eb;
    border-radius: 9px;
    selection-background-color: #8bb5f5;
}
QLineEdit:focus {
    background: #ffffff;
    border-color: #4c82dc;
}
QTextEdit {
    color: #344054;
    background: #ffffff;
    border: 1px solid #dce2eb;
    border-radius: 9px;
    padding: 9px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 12px;
}
QToolButton#ExpandButton {
    min-width: 31px;
    min-height: 31px;
    color: #526078;
    background: #ffffff;
    border: 1px solid #d7dde8;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 700;
}
QToolButton#ExpandButton:hover {
    color: #1d58ba;
    background: #f2f6fd;
    border-color: #a9bde0;
}
QToolButton#PinButton {
    min-width: 28px;
    min-height: 28px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
}
QToolButton#PinButton:hover {
    background: #f1f5fb;
    border-color: #d9e2f0;
}
QToolButton#PinButton:checked {
    background: #eaf2ff;
    border-color: #b8cff4;
}
QCheckBox {
    spacing: 8px;
    font-weight: 600;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    background: #ffffff;
    border: 1px solid #aeb8c7;
    border-radius: 4px;
}
QCheckBox::indicator:hover {
    border-color: #2767d7;
}
QCheckBox::indicator:checked {
    background: #2767d7;
    border-color: #2767d7;
    image: url(__CHECK_ICON__);
}
QCheckBox::indicator:indeterminate {
    background: #2767d7;
    border-color: #2767d7;
    image: url(__MINUS_ICON__);
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 2px;
}
QScrollBar::handle:vertical {
    min-height: 35px;
    background: #c7ced9;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #aeb8c7;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QProgressBar {
    height: 7px;
    border: none;
    border-radius: 3px;
    background: #e3e7ed;
    text-align: center;
}
QProgressBar::chunk {
    background: #3478e5;
    border-radius: 3px;
}
"""
STYLE_SHEET = STYLE_SHEET.replace(
    "__CHECK_ICON__",
    (PROJECT_ROOT / "src" / "assets" / "check.svg").as_posix(),
).replace(
    "__MINUS_ICON__",
    (PROJECT_ROOT / "src" / "assets" / "minus.svg").as_posix(),
)


def write_json_atomic(path: Path, data: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_pinned_app_ids(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        values = data.get("pinned_app_ids", []) if isinstance(data, dict) else []
        return {int(value) for value in values}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return set()


def save_pinned_app_ids(path: Path, app_ids: set[int]) -> None:
    write_json_atomic(path, {"pinned_app_ids": sorted(app_ids)})


def unique_paths(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = os.path.normcase(os.path.normpath(value))
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def account_from_path(value: str) -> str | None:
    parts = Path(value).parts
    for index, part in enumerate(parts):
        if part.casefold() == "userdata" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def normalized_search_text(value: str) -> str:
    """Case-fold text and remove spacing/punctuation for forgiving searches."""
    return "".join(character for character in value.casefold() if character.isalnum())


def is_search_subsequence(query: str, value: str) -> bool:
    """Return whether every query character occurs in order within value."""
    if not query:
        return True
    characters = iter(value)
    return all(any(candidate == wanted for candidate in characters) for wanted in query)


def format_backup_time(value: str | None) -> str:
    if not value:
        return "无"
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


class TaskThread(QThread):
    result_ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, task: Callable[[Callable[[int, int, str], None]], object], parent: QWidget) -> None:
        super().__init__(parent)
        self.task = task

    def run(self) -> None:
        try:
            self.result_ready.emit(self.task(self.progress.emit))
        except Exception as error:  # Errors are shown in the main UI thread.
            self.failed.emit(str(error))


class StableWidthScrollArea(QScrollArea):
    """Keep the content width tied to the viewport regardless of child size hints."""

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        widget.setMinimumWidth(0)
        self.sync_content_width()
        QTimer.singleShot(0, self.sync_content_width)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.sync_content_width()

    def sync_content_width(self) -> None:
        content = self.widget()
        if content is None:
            return
        width = max(0, self.viewport().width())
        if content.minimumWidth() != width or content.maximumWidth() != width:
            content.setFixedWidth(width)


class ChangeProgressDialog(QDialog):
    def __init__(self, total: int, parent: QWidget, title: str = "检测所有更改") -> None:
        super().__init__(parent)
        self.complete = False
        self.total = total
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(720, 500)
        self.resize(780, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel("正在检测游戏数据")
        title.setObjectName("GameTitle")
        layout.addWidget(title)
        self.overall_label = QLabel(f"总进度 0/{total}")
        self.overall_label.setObjectName("Muted")
        layout.addWidget(self.overall_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.game_label = QLabel("当前游戏：等待开始")
        self.game_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.game_label)
        self.item_label = QLabel("当前项目：—")
        self.item_label.setObjectName("Muted")
        self.item_label.setWordWrap(True)
        layout.addWidget(self.item_label)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("检测日志将在这里实时显示……")
        layout.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok_button = QPushButton("确定")
        self.ok_button.setObjectName("PrimaryButton")
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

    def update_progress(self, current: int, total: int, message: str) -> None:
        self.total = total
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.overall_label.setText(f"总进度 {current}/{total}")
        if "：" in message:
            game, detail = message.split("：", 1)
            self.game_label.setText(f"当前游戏：{game}")
            self.item_label.setText(f"当前项目：{detail}")
        else:
            self.item_label.setText(f"当前项目：{message}")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{timestamp}] {message}")
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def finish(self, summary: str) -> None:
        self.complete = True
        self.progress.setValue(self.progress.maximum())
        self.overall_label.setText(f"总进度 {self.total}/{self.total}")
        self.game_label.setText("检测完成")
        self.item_label.setText(summary)
        self.log.append(f"\n{summary}")
        self.ok_button.setEnabled(True)
        self.ok_button.setFocus()

    def fail(self, message: str) -> None:
        self.complete = True
        self.game_label.setText("检测失败")
        self.item_label.setText(message)
        self.log.append(f"\n检测失败：{message}")
        self.ok_button.setEnabled(True)

    def reject(self) -> None:
        if self.complete:
            super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.complete:
            event.accept()
        else:
            event.ignore()


class HistoryPackageOptionsDialog(QDialog):
    """Ask whether a ZIP should contain a delta or the complete point-in-time state."""

    def __init__(
        self,
        point: HistoryPoint | None,
        parent: QWidget,
        selected_count: int = 1,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("打包历史存档")
        self.setModal(True)
        self.setMinimumWidth(570)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(13)
        title_text = (
            f"打包 {point.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
            if point is not None else
            f"打包选中的 {selected_count} 个时间点"
        )
        title = QLabel(title_text)
        title.setObjectName("GameTitle")
        layout.addWidget(title)
        explanation = QLabel(
            "请选择 ZIP 中需要包含的内容。完整状态会为每个游戏的本地存档和每个账号，"
            "选择对应时间点或更早的最后一份记录。"
        )
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        if point is None:
            updated_text = "仅打包各个所选时间点更新的存档"
            complete_text = "打包各个所选时间点状态的全部存档"
        else:
            updated_text = "仅打包这个时间点更新的存档"
            complete_text = "打包这个时间点状态的全部存档"
        self.updated_only = QRadioButton(updated_text)
        self.complete = QRadioButton(complete_text)
        self.complete.setChecked(True)
        layout.addWidget(self.updated_only)
        layout.addWidget(self.complete)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        package_button = buttons.addButton("选择保存位置", QDialogButtonBox.ButtonRole.AcceptRole)
        package_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def complete_state(self) -> bool:
        return self.complete.isChecked()


class HistoryDialog(QDialog):
    """Browse timestamped backups and export point-in-time ZIP archives."""

    def __init__(
        self,
        backup_root: Path,
        parent: QWidget | None = None,
        game_directory: Path | None = None,
        game_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.backup_root = backup_root.resolve()
        self.game_directory = game_directory.resolve() if game_directory is not None else None
        self.game_name = game_name
        self.backup_changed = False
        self.sources_restored = False
        self.points: list[HistoryPoint] = []
        self.row_checkboxes: list[QCheckBox] = []
        self.point_checkboxes: dict[str, QCheckBox] = {}
        self.updating_select_all = False
        self.setWindowTitle(f"{game_name} · 历史版本" if game_name else "历史版本")
        self.setMinimumSize(780, 520)
        self.resize(900, 620)

        page = QVBoxLayout(self)
        page.setContentsMargins(24, 22, 24, 20)
        page.setSpacing(12)
        title = QLabel(f"{game_name} · 历史版本" if game_name else "历史版本")
        title.setObjectName("PageTitle")
        page.addWidget(title)
        subtitle_text = (
            "选择一个时间点可将该游戏当时的完整存档状态复制回原目录。恢复会覆盖同名文件，"
            "但不会删除原目录中的额外文件。"
            if self.game_directory is not None else
            "每行代表一个备份时间点。清除会删除该时间点的备份副本，但不会改动游戏原始存档。"
        )
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        page.addWidget(subtitle)

        controls = QFrame()
        controls.setObjectName("FilterBar")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(14, 9, 10, 9)
        controls_layout.setSpacing(9)
        self.select_all = QCheckBox("全选")
        self.select_all.stateChanged.connect(self.toggle_select_all)
        controls_layout.addWidget(self.select_all)
        self.summary = QLabel()
        self.summary.setObjectName("CountText")
        controls_layout.addWidget(self.summary)
        controls_layout.addStretch(1)
        self.import_button = QPushButton("从压缩包导入")
        self.import_button.clicked.connect(self.import_package)
        self.import_button.setVisible(self.game_directory is None)
        controls_layout.addWidget(self.import_button)
        self.delete_selected_button = QPushButton("删除选中")
        self.delete_selected_button.clicked.connect(self.delete_selected)
        controls_layout.addWidget(self.delete_selected_button)
        self.package_selected_button = QPushButton("打包选中")
        self.package_selected_button.clicked.connect(self.package_selected)
        self.package_selected_button.setVisible(self.game_directory is None)
        controls_layout.addWidget(self.package_selected_button)
        page.addWidget(controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 8, 0)
        self.rows_layout.setSpacing(8)
        scroll.setWidget(self.rows_host)
        page.addWidget(scroll, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        page.addLayout(close_row)
        self.reload()

    def reload(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.row_checkboxes.clear()
        self.point_checkboxes.clear()
        try:
            self.points = (
                list_game_history(self.backup_root, self.game_directory)
                if self.game_directory is not None else
                list_history(self.backup_root)
            )
        except OSError as error:
            self.points = []
            QMessageBox.critical(self, "无法读取历史版本", str(error))

        if not self.points:
            empty = QLabel("还没有历史备份。完成一次备份后会在这里显示。")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(160)
            self.rows_layout.addWidget(empty)
        else:
            for point in self.points:
                self._add_row(point)
        self.rows_layout.addStretch(1)
        self.update_selection_state()

    def _add_row(self, point: HistoryPoint) -> None:
        row = QFrame()
        row.setObjectName("HistoryRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 9, 10, 9)
        layout.setSpacing(10)

        checkbox = QCheckBox()
        checkbox.setToolTip("选择此时间点")
        checkbox.stateChanged.connect(lambda _state: self.update_selection_state())
        self.row_checkboxes.append(checkbox)
        self.point_checkboxes[point.timestamp_key] = checkbox
        layout.addWidget(checkbox)
        timestamp = QLabel(point.timestamp.strftime("%Y-%m-%d  %H:%M:%S"))
        timestamp.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(timestamp)
        details = QLabel(
            f"{point.game_count} 个游戏 · {len(point.snapshots)} 份存档"
            f" · {point.archived_count} 份历史目录"
        )
        details.setObjectName("Muted")
        layout.addWidget(details)
        layout.addStretch(1)

        clear_button = QPushButton("清除")
        clear_button.setObjectName("DangerButton")
        clear_button.clicked.connect(lambda _checked=False, item=point: self.clear_point(item))
        layout.addWidget(clear_button)
        if self.game_directory is None:
            package_button = QPushButton("打包")
            package_button.clicked.connect(lambda _checked=False, item=point: self.package_point(item))
            layout.addWidget(package_button)
        else:
            restore_button = QPushButton("恢复")
            restore_button.setObjectName("WarningButton")
            restore_button.clicked.connect(lambda _checked=False, item=point: self.restore_point(item))
            layout.addWidget(restore_button)
        self.rows_layout.addWidget(row)

    def selected_points(self) -> list[HistoryPoint]:
        return [
            point for point in self.points
            if self.point_checkboxes.get(point.timestamp_key) is not None
            and self.point_checkboxes[point.timestamp_key].isChecked()
        ]

    def toggle_select_all(self, state: int) -> None:
        if self.updating_select_all:
            return
        checked = state != Qt.CheckState.Unchecked.value
        for checkbox in self.row_checkboxes:
            checkbox.setChecked(checked)

    def update_selection_state(self) -> None:
        selected_count = sum(checkbox.isChecked() for checkbox in self.row_checkboxes)
        total = len(self.row_checkboxes)
        if total == 0 or selected_count == 0:
            state = Qt.CheckState.Unchecked
        elif selected_count == total:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.updating_select_all = True
        self.select_all.setCheckState(state)
        self.updating_select_all = False
        self.summary.setText(f"已选 {selected_count} / {total} 个时间点")
        self.delete_selected_button.setEnabled(selected_count > 0)
        self.package_selected_button.setEnabled(selected_count > 0)

    def delete_selected(self) -> None:
        points = self.selected_points()
        if not points:
            return
        snapshot_count = sum(len(point.snapshots) for point in points)
        answer = QMessageBox.question(
            self,
            "确认删除选中的历史版本",
            f"确定删除选中的 {len(points)} 个时间点、共 {snapshot_count} 份备份吗？\n\n"
            "如果其中包含当前备份，其哈希记录也会同步移除；游戏原始存档不会被改动。"
            "此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed_count = 0
        failures: list[str] = []
        for point in points:
            try:
                result = clear_history_point(
                    self.backup_root,
                    point.timestamp_key,
                    self.game_directory,
                )
                removed_count += len(result.removed)
            except (OSError, ValueError) as error:
                failures.append(f"{point.timestamp_key}：{error}")
        self.backup_changed = self.backup_changed or removed_count > 0
        self.reload()
        if failures:
            QMessageBox.warning(
                self,
                "部分删除失败",
                f"已删除 {removed_count} 份备份目录，{len(failures)} 个时间点失败。\n\n"
                + "\n".join(failures[:8]),
            )
        else:
            QMessageBox.information(self, "删除完成", f"已删除 {removed_count} 份备份目录。")

    def package_selected(self) -> None:
        points = self.selected_points()
        if not points:
            return
        options = HistoryPackageOptionsDialog(None, self, len(points))
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        mode = "state" if options.complete_state else "updated"
        suggested = self.backup_root / "exports" / f"steam-saves-{len(points)}-points-{mode}.zip"
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存选中的历史存档 ZIP",
            str(suggested),
            "ZIP 压缩包 (*.zip)",
        )
        if not filename:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        error_message: str | None = None
        result = None
        try:
            result = package_history_points(
                self.backup_root,
                [point.timestamp_key for point in points],
                Path(filename),
                options.complete_state,
            )
        except (OSError, ValueError, RuntimeError) as error:
            error_message = str(error)
        finally:
            QApplication.restoreOverrideCursor()
        if error_message is not None:
            QMessageBox.critical(self, "打包失败", error_message)
            return
        assert result is not None
        QMessageBox.information(
            self,
            "打包完成",
            f"已将 {len(points)} 个时间点打包为一个 ZIP，共包含 {result.snapshot_count} 份存档、"
            f"{result.file_count} 个文件。\n\n{result.output}",
        )

    def import_package(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入 Steam 存档备份",
            str(self.backup_root / "exports"),
            "ZIP 压缩包 (*.zip)",
        )
        if not filename:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        error_message: str | None = None
        result = None
        try:
            result = import_history_package(self.backup_root, Path(filename))
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
            error_message = str(error)
        finally:
            QApplication.restoreOverrideCursor()
        if error_message is not None:
            QMessageBox.critical(self, "导入失败", error_message)
            return
        assert result is not None
        self.backup_changed = True
        self.reload()
        QMessageBox.information(
            self,
            "导入完成",
            f"已导入 {len(result.imported)} 份存档，包含 {len(result.time_points)} 个时间点。",
        )

    def restore_point(self, point: HistoryPoint) -> None:
        if self.game_directory is None:
            return
        answer = QMessageBox.warning(
            self,
            "确认恢复历史版本",
            f"确定将 {point.timestamp.strftime('%Y-%m-%d %H:%M:%S')} 时的完整存档状态"
            "复制回原目录吗？\n\n同名文件会被覆盖；原目录中的额外文件不会删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        error_message: str | None = None
        result = None
        try:
            result = restore_game_history_point(
                self.backup_root,
                self.game_directory,
                point.timestamp_key,
            )
        except (OSError, ValueError) as error:
            error_message = str(error)
        finally:
            QApplication.restoreOverrideCursor()
        if error_message is not None:
            QMessageBox.critical(self, "恢复失败", error_message)
            return
        assert result is not None
        self.sources_restored = True
        extra = (
            "\n以下分区缺少旧版路径映射，已跳过：" + "、".join(result.skipped_scopes)
            if result.skipped_scopes else ""
        )
        QMessageBox.information(
            self,
            "恢复完成",
            f"已恢复 {result.restored_sources} 个来源、{result.restored_files} 个文件。{extra}",
        )

    def clear_point(self, point: HistoryPoint) -> None:
        answer = QMessageBox.question(
            self,
            "确认清除历史版本",
            f"确定删除 {point.timestamp.strftime('%Y-%m-%d %H:%M:%S')} 的 "
            f"{len(point.snapshots)} 份备份吗？\n\n"
            "如果其中包含当前备份，其哈希记录也会同步移除；游戏原始存档不会被改动。"
            "此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = clear_history_point(
                self.backup_root,
                point.timestamp_key,
                self.game_directory,
            )
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "清除失败", str(error))
            return
        QMessageBox.information(
            self,
            "清除完成",
            f"已删除 {len(result.removed)} 份备份目录。",
        )
        self.backup_changed = self.backup_changed or bool(result.removed)
        self.reload()

    def package_point(self, point: HistoryPoint) -> None:
        options = HistoryPackageOptionsDialog(point, self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        mode = "state" if options.complete_state else "updated"
        suggested = self.backup_root / "exports" / f"steam-saves-{point.timestamp_key}-{mode}.zip"
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存历史存档 ZIP",
            str(suggested),
            "ZIP 压缩包 (*.zip)",
        )
        if not filename:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        error_message: str | None = None
        result = None
        try:
            result = package_history(
                self.backup_root,
                point.timestamp_key,
                Path(filename),
                options.complete_state,
            )
        except (OSError, ValueError, RuntimeError) as error:
            error_message = str(error)
        finally:
            QApplication.restoreOverrideCursor()
        if error_message is not None:
            QMessageBox.critical(self, "打包失败", error_message)
            return
        assert result is not None
        QMessageBox.information(
            self,
            "打包完成",
            f"已打包 {result.game_count} 个游戏、{result.snapshot_count} 份存档、"
            f"{result.file_count} 个文件。\n\n{result.output}",
        )


class ClickablePath(QLabel):
    activated = Signal(str)

    def __init__(
        self,
        path: str | None,
        text: str | None = None,
        word_wrap: bool = True,
    ) -> None:
        super().__init__()
        self.path: str | None = None
        self.setObjectName("PathLink")
        self.setWordWrap(word_wrap)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored if word_wrap else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_target(path, text)

    def set_target(self, path: str | None, text: str | None = None) -> None:
        self.path = path
        self.setText(text if text is not None else (path or "无"))
        active = bool(path)
        self.setProperty("active", active)
        self.setToolTip(f"在文件资源管理器中打开\n{path}" if path else "当前没有可打开的备份")
        self.setCursor(QCursor(
            Qt.CursorShape.PointingHandCursor if active else Qt.CursorShape.ArrowCursor
        ))
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().mouseReleaseEvent(event)
        if self.path and event.button() == Qt.MouseButton.LeftButton and not self.hasSelectedText():
            self.activated.emit(self.path)
        event.accept()


class ElidedLabel(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.full_text = text
        self.setToolTip(text)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.setText(
            self.fontMetrics().elidedText(
                self.full_text,
                Qt.TextElideMode.ElideRight,
                max(0, self.width() - 2),
            )
        )
        super().resizeEvent(event)


class GameCard(QFrame):
    selection_changed = Signal()
    pin_changed = Signal(int, bool)
    backup_requested = Signal(int)
    path_requested = Signal(str)
    history_requested = Signal(int)
    expansion_changed = Signal()

    def __init__(self, game: dict[str, object], pinned: bool = False) -> None:
        super().__init__()
        self.game = game
        self.app_id = int(game.get("app_id", 0))
        self.game_name = str(game.get("name", "Unknown"))
        self.backup_directory = game_backup_directory(game, BACKUP_ROOT)
        try:
            metadata = backup_metadata(game, BACKUP_ROOT)
            self.last_backup_at = metadata.get("last_backup_at")
        except OSError:
            self.last_backup_at = None
        local = game.get("local", {})
        userdata = game.get("steam_userdata", {})
        local_paths = unique_paths([
            *local.get("directories", []),
            *local.get("files", []),
        ])
        remote_paths = unique_paths(list(userdata.get("app_directories", [])))
        if not remote_paths:
            remote_paths = unique_paths([
                *userdata.get("remote_directories", []),
                *userdata.get("manifest_directories", []),
                *userdata.get("files", []),
            ])
        remote_groups: dict[str, list[str]] = {}
        for path in remote_paths:
            remote_groups.setdefault(account_from_path(path) or "未知账号", []).append(path)

        self.has_local = bool(local.get("found"))
        self.has_remote = bool(remote_paths)
        self.setObjectName("GameCard")
        # The expanded details contain long paths.  Their size hints must not
        # participate in the scroll area's horizontal layout, otherwise Qt can
        # briefly relayout every sibling card before the viewport width wins.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 9, 13, 9)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(9)
        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("选择此游戏")
        self.checkbox.stateChanged.connect(lambda _state: self.selection_changed.emit())
        title_row.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self.pin_button = QToolButton()
        self.pin_button.setObjectName("PinButton")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(pinned)
        self.pin_button.setIconSize(QSize(18, 18))
        self._refresh_pin_button(pinned)
        self.pin_button.toggled.connect(self._pin_toggled)
        title_row.addWidget(self.pin_button, 0, Qt.AlignmentFlag.AlignVCenter)

        title = ElidedLabel(self.game_name)
        title.setObjectName("GameTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setMinimumWidth(120)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(title, 1)

        local_summary = QLabel(f"本地数据：{len(local_paths)} 条")
        local_summary.setObjectName("SummaryBadge")
        local_summary.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(local_summary)

        remote_summary = QLabel(f"远程数据：{len(remote_groups)} 账号，{len(remote_paths)} 条")
        remote_summary.setObjectName("SummaryBadge")
        remote_summary.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(remote_summary)

        app_id_label = QLabel(f"Steam ID  {self.app_id}")
        app_id_label.setObjectName("AppId")
        app_id_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(app_id_label)

        self.change_badge = QLabel()
        self.change_badge.setObjectName("ChangeBadge")
        self.change_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(self.change_badge)

        self.last_backup_label = ClickablePath(None, word_wrap=False)
        self.last_backup_label.setObjectName("LastBackup")
        self.last_backup_label.activated.connect(self.path_requested.emit)
        title_row.addWidget(self.last_backup_label)
        self.set_change_state("unchecked", self.last_backup_at)

        backup_button = QPushButton("备份此项")
        backup_button.setObjectName("CardButton")
        backup_button.clicked.connect(lambda _checked=False: self.backup_requested.emit(self.app_id))
        self.backup_button = backup_button
        title_row.addWidget(backup_button)

        self.expand_button = QToolButton()
        self.expand_button.setObjectName("ExpandButton")
        self.expand_button.setText("∨")
        self.expand_button.setToolTip("展开路径")
        self.expand_button.setCheckable(True)
        self.expand_button.toggled.connect(self.set_expanded)
        title_row.addWidget(self.expand_button)
        layout.addLayout(title_row)

        self.details = QWidget()
        self.details.setMinimumWidth(0)
        self.details.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content = QGridLayout(self.details)
        content.setContentsMargins(29, 7, 0, 3)
        content.setHorizontalSpacing(15)
        content.setVerticalSpacing(7)
        content.setColumnStretch(1, 1)

        row = 0
        self._add_path_group(content, row, "本地数据：", local_paths)
        row += 1

        if remote_groups:
            first = True
            for account, paths in remote_groups.items():
                label = f"Steam 账号 {account}：" if first else f"账号 {account}："
                self._add_path_group(content, row, label, paths)
                row += 1
                first = False
        else:
            self._add_path_group(content, row, "Steam 账号数据：", [])
            row += 1

        registry = list(local.get("registry_keys", []))
        if registry:
            self._add_text_group(content, row, "注册表：", registry)
            row += 1

        backup_section = QLabel("备份文件夹：")
        backup_section.setObjectName("SectionLabel")
        backup_section.setMinimumWidth(112)
        backup_section.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content.addWidget(backup_section, row, 0, Qt.AlignmentFlag.AlignTop)
        self.backup_path_link = ClickablePath(None)
        self.backup_path_link.activated.connect(self.path_requested.emit)
        content.addWidget(self.backup_path_link, row, 1)
        history_button = QPushButton("查看历史版本")
        history_button.setObjectName("CardButton")
        history_button.clicked.connect(
            lambda _checked=False: self.history_requested.emit(self.app_id)
        )
        content.addWidget(history_button, row, 2, Qt.AlignmentFlag.AlignRight)
        self._refresh_backup_links()
        self.details.hide()
        layout.addWidget(self.details)

    def set_expanded(self, expanded: bool) -> None:
        self.details.setVisible(expanded)
        self.expand_button.setText("∧" if expanded else "∨")
        self.expand_button.setToolTip("收起路径" if expanded else "展开路径")
        self.expansion_changed.emit()

    def _pin_toggled(self, pinned: bool) -> None:
        self._refresh_pin_button(pinned)
        self.pin_changed.emit(self.app_id, pinned)

    def _refresh_pin_button(self, pinned: bool) -> None:
        self.pin_button.setIcon(QIcon(str(PIN_FILLED_ICON if pinned else PIN_OUTLINE_ICON)))
        self.pin_button.setToolTip("取消置顶" if pinned else "置顶此游戏")

    def is_pinned(self) -> bool:
        return self.pin_button.isChecked()

    def set_change_state(self, state: str, last_backup_at: str | None = None) -> None:
        labels = {
            "unchecked": "未检测",
            "clean": "无修改",
            "changed": "有修改",
        }
        self.change_state = state
        self.last_backup_at = last_backup_at
        self.change_badge.setText(labels[state])
        self.change_badge.setProperty("state", state)
        self.change_badge.style().unpolish(self.change_badge)
        self.change_badge.style().polish(self.change_badge)
        self._refresh_backup_links()

    def _refresh_backup_links(self) -> None:
        available = bool(self.last_backup_at) and self.backup_directory.is_dir()
        target = str(self.backup_directory.resolve()) if available else None
        self.last_backup_label.set_target(
            target,
            f"上次备份：{format_backup_time(self.last_backup_at)}",
        )
        if hasattr(self, "backup_path_link"):
            self.backup_path_link.set_target(target, target or "无")

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self.expand_button.setChecked(not self.expand_button.isChecked())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def matches_search(self, query: str) -> bool:
        normalized = normalized_search_text(query)
        if not normalized:
            return True
        return (
            is_search_subsequence(normalized, normalized_search_text(self.game_name))
            or is_search_subsequence(normalized, str(self.app_id))
        )

    def _add_path_group(
        self,
        grid: QGridLayout,
        row: int,
        label: str,
        paths: list[str],
    ) -> None:
        section = QLabel(label)
        section.setObjectName("SectionLabel")
        section.setMinimumWidth(112)
        section.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        grid.addWidget(section, row, 0, Qt.AlignmentFlag.AlignTop)
        value_box = QVBoxLayout()
        value_box.setSpacing(3)
        if paths:
            for path in paths:
                link = ClickablePath(path)
                link.activated.connect(self.path_requested.emit)
                value_box.addWidget(link)
        else:
            empty = QLabel("无")
            empty.setObjectName("EmptyValue")
            empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            value_box.addWidget(empty)
        grid.addLayout(value_box, row, 1)

    @staticmethod
    def _add_text_group(
        grid: QGridLayout,
        row: int,
        label: str,
        items: list[str],
    ) -> None:
        section = QLabel(label)
        section.setObjectName("SectionLabel")
        section.setMinimumWidth(112)
        grid.addWidget(section, row, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel("\n".join(items))
        text.setObjectName("Muted")
        text.setWordWrap(True)
        text.setMinimumWidth(0)
        text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(text, row, 1)

    def is_selected(self) -> bool:
        return self.checkbox.isChecked()

    def set_selected(self, selected: bool) -> None:
        self.checkbox.setChecked(selected)

    def set_actions_enabled(self, enabled: bool) -> None:
        self.backup_button.setEnabled(enabled)


class SteamBackupWindow(QMainWindow):
    def __init__(self, pinned_games_path: Path = PINNED_GAMES_PATH) -> None:
        super().__init__()
        self.pinned_games_path = pinned_games_path
        self.pinned_app_ids = load_pinned_app_ids(pinned_games_path)
        self.report: dict[str, object] | None = None
        self.cards: list[GameCard] = []
        self.worker: TaskThread | None = None
        self.change_dialog: ChangeProgressDialog | None = None
        self.updating_select_all = False

        self.setWindowTitle("Steam 存档备份")
        self.resize(1120, 780)
        self.setMinimumSize(820, 580)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(28, 24, 28, 20)
        page.setSpacing(13)

        title = QLabel("Steam 存档备份")
        title.setObjectName("PageTitle")
        page.addWidget(title)
        subtitle_row = QHBoxLayout()
        subtitle = QLabel("扫描本机 Steam 游戏，查看存档位置，并按本地或账号分别保留历史版本。")
        subtitle.setObjectName("PageSubtitle")
        subtitle_row.addWidget(subtitle)
        subtitle_row.addStretch(1)
        self.history_button = QPushButton("查看历史版本")
        self.history_button.setToolTip("浏览、清除或打包已经备份的历史时间点")
        self.history_button.clicked.connect(self.show_history)
        subtitle_row.addWidget(self.history_button)
        page.addLayout(subtitle_row)

        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(8)
        self.scan_button = QPushButton("检测所有游戏")
        self.scan_button.setObjectName("PrimaryButton")
        self.scan_button.clicked.connect(self.start_scan)
        toolbar_layout.addWidget(self.scan_button)
        self.selected_changes_button = QPushButton("检测选中更改")
        self.selected_changes_button.clicked.connect(self.start_selected_change_detection)
        self.selected_changes_button.setEnabled(False)
        toolbar_layout.addWidget(self.selected_changes_button)
        self.changes_button = QPushButton("检测所有更改")
        self.changes_button.setObjectName("PrimaryButton")
        self.changes_button.clicked.connect(lambda _checked=False: self.start_change_detection(None))
        self.changes_button.setEnabled(False)
        toolbar_layout.addWidget(self.changes_button)
        toolbar_layout.addStretch(1)
        self.selected_backup_button = QPushButton("备份选中")
        self.selected_backup_button.clicked.connect(self.backup_selected)
        toolbar_layout.addWidget(self.selected_backup_button)
        self.all_backup_button = QPushButton("备份所有")
        self.all_backup_button.setObjectName("PrimaryButton")
        self.all_backup_button.clicked.connect(self.backup_all)
        toolbar_layout.addWidget(self.all_backup_button)
        page.addWidget(toolbar)

        filter_bar = QFrame()
        filter_bar.setObjectName("FilterBar")
        filter_layout = QHBoxLayout(filter_bar)
        filter_layout.setContentsMargins(17, 9, 12, 9)
        filter_layout.setSpacing(8)
        self.select_all = QCheckBox("全选")
        self.select_all.stateChanged.connect(self.toggle_select_all)
        filter_layout.addWidget(self.select_all)
        self.count_label = QLabel()
        self.count_label.setObjectName("CountText")
        filter_layout.addWidget(self.count_label)
        filter_layout.addStretch(1)
        filter_layout.addWidget(QLabel("筛选"))
        self.search_filter = QLineEdit()
        self.search_filter.setPlaceholderText("搜索游戏名称 / Steam ID")
        self.search_filter.setClearButtonEnabled(True)
        self.search_filter.setMinimumWidth(230)
        self.search_filter.setMaximumWidth(310)
        self.search_filter.textChanged.connect(lambda _text: self.apply_filters())
        filter_layout.addWidget(self.search_filter)
        self.local_filter = QPushButton("有本地")
        self.local_filter.setObjectName("FilterChip")
        self.local_filter.setCheckable(True)
        self.local_filter.toggled.connect(self.apply_filters)
        filter_layout.addWidget(self.local_filter)
        self.remote_filter = QPushButton("有远程 / 账号")
        self.remote_filter.setObjectName("FilterChip")
        self.remote_filter.setCheckable(True)
        self.remote_filter.toggled.connect(self.apply_filters)
        filter_layout.addWidget(self.remote_filter)
        page.addWidget(filter_bar)

        self.scroll = StableWidthScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.cards_host = QWidget()
        self.cards_host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.cards_layout.setContentsMargins(0, 1, 7, 1)
        self.cards_layout.setSpacing(11)
        self.empty_label = QLabel("没有符合当前筛选条件的游戏")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(120)
        self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch(1)
        self.scroll.setWidget(self.cards_host)
        page.addWidget(self.scroll, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        page.addWidget(self.progress)
        self.status = QLabel("就绪")
        self.status.setObjectName("StatusText")
        page.addWidget(self.status)

        self.load_existing_report()

    def load_existing_report(self) -> None:
        if not REPORT_PATH.is_file():
            self.set_backup_buttons_enabled(False)
            self.status.setText("尚未检测，点击“检测所有游戏”开始。")
            return
        try:
            self.set_report(load_report(REPORT_PATH))
            self.status.setText(f"已载入现有列表：{len(self.cards)} 个游戏")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.set_backup_buttons_enabled(False)
            self.status.setText(f"现有扫描结果无法读取：{error}")

    def set_report(self, report: dict[str, object]) -> None:
        self.report = report
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cards.clear()
        for game in report.get("games", []):
            card = GameCard(game, int(game.get("app_id", 0)) in self.pinned_app_ids)
            card.selection_changed.connect(self.update_select_all_state)
            card.pin_changed.connect(self.set_game_pinned)
            card.backup_requested.connect(self.backup_one)
            card.path_requested.connect(self.reveal_path)
            card.history_requested.connect(self.show_game_history)
            card.expansion_changed.connect(self.sync_cards_height)
            self.cards.append(card)
            self.cards_layout.addWidget(card)
        self.sort_cards()
        self.empty_label = QLabel("没有符合当前筛选条件的游戏")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(120)
        self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch(1)
        self.apply_filters()
        self.set_backup_buttons_enabled(True)

    def visible_cards(self) -> list[GameCard]:
        return [card for card in self.cards if not card.isHidden()]

    @staticmethod
    def card_sort_key(card: GameCard) -> tuple[int, int, str]:
        ranks = {"changed": 0, "unchecked": 1, "clean": 2}
        return (0 if card.is_pinned() else 1, ranks[card.change_state], card.game_name.casefold())

    def sort_cards(self) -> None:
        self.cards.sort(key=self.card_sort_key)
        for card in self.cards:
            self.cards_layout.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.cards_layout.insertWidget(index, card)
        QTimer.singleShot(0, self.sync_cards_height)

    def set_game_pinned(self, app_id: int, pinned: bool) -> None:
        previous = set(self.pinned_app_ids)
        if pinned:
            self.pinned_app_ids.add(app_id)
        else:
            self.pinned_app_ids.discard(app_id)
        try:
            save_pinned_app_ids(self.pinned_games_path, self.pinned_app_ids)
        except OSError as error:
            self.pinned_app_ids = previous
            card = next((item for item in self.cards if item.app_id == app_id), None)
            if card is not None:
                card.pin_button.blockSignals(True)
                card.pin_button.setChecked(app_id in previous)
                card._refresh_pin_button(app_id in previous)
                card.pin_button.blockSignals(False)
            QMessageBox.warning(self, "无法保存置顶状态", str(error))
            return
        self.sort_cards()
        card = next((item for item in self.cards if item.app_id == app_id), None)
        if card is not None:
            self.status.setText(f"已{'置顶' if pinned else '取消置顶'}：{card.game_name}")

    def apply_filters(self) -> None:
        search = self.search_filter.text()
        local_only = self.local_filter.isChecked()
        remote_only = self.remote_filter.isChecked()
        visible_count = 0
        for card in self.cards:
            visible = (
                card.matches_search(search)
                and (not local_only or card.has_local)
                and (not remote_only or card.has_remote)
            )
            card.setVisible(visible)
            visible_count += int(visible)
        self.empty_label.setVisible(bool(self.cards) and visible_count == 0)
        self.count_label.setText(f"{visible_count} / {len(self.cards)} 个游戏")
        self.update_select_all_state()
        # Cards are populated before the top-level window is polished. The
        # first pass can therefore use pre-style heights; expanding one card
        # would then correct every sibling at once. Refresh the vertical extent
        # after styling, while the horizontal constraint remains disabled.
        QTimer.singleShot(0, self.sync_cards_height)

    def sync_cards_height(self) -> None:
        self.cards_host.ensurePolished()
        for card in self.visible_cards():
            card.ensurePolished()
            card_layout = card.layout()
            if card_layout is not None:
                card_layout.invalidate()
                card_layout.activate()
        self.cards_layout.invalidate()
        self.cards_layout.activate()
        preferred_height = self.cards_layout.sizeHint().height()
        self.cards_host.setMinimumHeight(preferred_height)
        self.cards_host.resize(
            self.cards_host.width(),
            max(preferred_height, self.scroll.viewport().height()),
        )
        self.cards_host.updateGeometry()

    def toggle_select_all(self, state: int) -> None:
        if self.updating_select_all:
            return
        selected = state != Qt.CheckState.Unchecked.value
        for card in self.visible_cards():
            card.set_selected(selected)

    def update_select_all_state(self) -> None:
        visible = self.visible_cards()
        selected = sum(card.is_selected() for card in visible)
        if not visible or selected == 0:
            state = Qt.CheckState.Unchecked
        elif selected == len(visible):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.updating_select_all = True
        self.select_all.setCheckState(state)
        self.updating_select_all = False

    def set_backup_buttons_enabled(self, enabled: bool) -> None:
        available = enabled and self.report is not None
        self.selected_changes_button.setEnabled(available)
        self.changes_button.setEnabled(available)
        self.selected_backup_button.setEnabled(available)
        self.all_backup_button.setEnabled(available)
        for card in self.cards:
            card.set_actions_enabled(available)

    def set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy)
        self.history_button.setEnabled(not busy)
        self.set_backup_buttons_enabled(not busy)
        self.local_filter.setEnabled(not busy)
        self.remote_filter.setEnabled(not busy)
        self.search_filter.setEnabled(not busy)
        self.select_all.setEnabled(not busy)
        if not busy:
            self.progress.hide()

    def run_task(
        self,
        task: Callable[[Callable[[int, int, str], None]], object],
        finished: Callable[[object], None],
        operation_name: str,
        progress_listener: Callable[[int, int, str], None] | None = None,
        failure_handler: Callable[[str], None] | None = None,
    ) -> None:
        self.set_busy(True)
        self.progress.show()
        self.worker = TaskThread(task, self)
        self.worker.progress.connect(self.show_progress)
        if progress_listener:
            self.worker.progress.connect(progress_listener)
        self.worker.result_ready.connect(finished)
        if failure_handler:
            self.worker.failed.connect(failure_handler)
        else:
            self.worker.failed.connect(lambda message: self.operation_failed(operation_name, message))
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def selected_app_ids(self) -> set[int]:
        return {card.app_id for card in self.cards if card.is_selected()}

    def start_scan(self) -> None:
        self.progress.setRange(0, 0)
        self.status.setText("正在检测所有 Steam 游戏和数据目录……")

        def task(_progress: Callable[[int, int, str], None]) -> dict[str, object]:
            if not MANIFEST_PATH.is_file():
                raise FileNotFoundError(f"找不到 Ludusavi 清单：{MANIFEST_PATH}")
            report = build_report(discover_steam_root(), MANIFEST_PATH)
            write_json_atomic(REPORT_PATH, report)
            return report

        self.run_task(task, self.scan_finished, "检测失败")

    def scan_finished(self, result: object) -> None:
        report = result
        self.set_report(report)
        self.set_busy(False)
        summary = report.get("summary", {})
        self.status.setText(
            f"检测完成：{summary.get('installed_apps', 0)} 个游戏 · "
            f"本地 {summary.get('with_local_data', 0)} · "
            f"远程/账号 {summary.get('with_steam_userdata', 0)}"
        )

    def start_selected_change_detection(self) -> None:
        selected = self.selected_app_ids()
        if not selected:
            QMessageBox.information(self, "未选择游戏", "请先勾选一个或多个游戏。")
            return
        self.start_change_detection(selected)

    def start_change_detection(self, app_ids: set[int] | None) -> None:
        if self.report is None:
            return
        report = dict(self.report)
        if app_ids is not None:
            report["games"] = [
                game for game in self.report.get("games", [])
                if int(game.get("app_id", 0)) in app_ids
            ]
        total = len(report.get("games", []))
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        selected_only = app_ids is not None
        self.status.setText(
            "正在检测选中游戏的已追踪路径……"
            if selected_only else
            "正在检测所有已追踪路径的更改……"
        )
        self.change_dialog = ChangeProgressDialog(
            total,
            self,
            "检测选中游戏更改" if selected_only else "检测所有更改",
        )
        self.change_dialog.show()

        def task(progress: Callable[[int, int, str], None]) -> object:
            return detect_report_changes(report, BACKUP_ROOT, progress=progress)

        self.run_task(
            task,
            lambda result: self.change_detection_finished(result, app_ids),
            "更改检测失败",
            progress_listener=self.change_dialog.update_progress,
            failure_handler=self.change_detection_failed,
        )

    def change_detection_finished(
        self,
        result: object,
        scanned_app_ids: set[int] | None = None,
    ) -> None:
        cards = {card.app_id: card for card in self.cards}
        changed_count = 0
        for item in result.completed:
            card = cards.get(int(item["app_id"]))
            if card is None:
                continue
            changed = bool(item["changed"])
            card.set_change_state(
                "changed" if changed else "clean",
                item.get("last_backup_at"),
            )
            changed_count += int(changed)
        self.sort_cards()
        for card in self.cards:
            if scanned_app_ids is None or card.app_id in scanned_app_ids:
                card.checkbox.blockSignals(True)
                card.set_selected(card.change_state == "changed")
                card.checkbox.blockSignals(False)
        self.update_select_all_state()
        self.set_busy(False)
        summary = (
            f"{'选中游戏' if scanned_app_ids is not None else '全部游戏'}更改检测完成："
            f"{changed_count} 个有修改 · "
            f"{len(result.completed) - changed_count} 个无修改 · "
            f"{len(result.failed)} 个检测失败"
        )
        self.status.setText(summary)
        if self.change_dialog is not None:
            self.change_dialog.finish(summary)

    def change_detection_failed(self, message: str) -> None:
        self.set_busy(False)
        self.status.setText(f"更改检测失败：{message}")
        if self.change_dialog is not None:
            self.change_dialog.fail(message)

    def backup_selected(self) -> None:
        selected = {card.app_id for card in self.cards if card.is_selected()}
        if not selected:
            QMessageBox.information(self, "未选择游戏", "请先勾选一个或多个游戏。")
            return
        self.start_backup(selected)

    def backup_all(self) -> None:
        self.start_backup(None)

    def backup_one(self, app_id: int) -> None:
        self.start_backup({app_id})

    def start_backup(self, app_ids: set[int] | None) -> None:
        if self.report is None:
            return
        batch_time = datetime.now().astimezone().isoformat(timespec="seconds")
        report = dict(self.report)
        if app_ids is not None:
            report["games"] = [
                game for game in self.report.get("games", [])
                if int(game.get("app_id", 0)) in app_ids
            ]
        total = len(report.get("games", []))
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.status.setText("正在准备备份……")

        def task(progress: Callable[[int, int, str], None]) -> object:
            return backup_report(
                report,
                BACKUP_ROOT,
                progress=progress,
                backed_up_at=batch_time,
            )

        self.run_task(task, self.backup_finished, "备份失败")

    def show_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.status.setText(f"{message}（{current}/{total}）")

    def backup_finished(self, result: object) -> None:
        self.set_busy(False)
        cards = {card.app_id: card for card in self.cards}
        successful = [*result.completed, *result.unchanged]
        for item in successful:
            app_id = int(item["app_id"])
            card = cards.get(app_id)
            if card is None:
                continue
            try:
                metadata = backup_metadata(card.game, BACKUP_ROOT)
                card.set_change_state("clean", metadata.get("last_backup_at"))
            except OSError:
                card.set_change_state("unchecked", card.last_backup_at)
        message = (
            f"备份完成：{len(result.completed)} 个已更新 · "
            f"{len(result.unchanged)} 个无变化 · "
            f"{len(result.skipped)} 个无数据 · "
            f"{len(result.failed)} 个失败"
        )
        self.status.setText(message)
        if result.failed:
            details = "\n".join(f"{item['name']}：{item['error']}" for item in result.failed[:8])
            QMessageBox.warning(self, "备份完成（存在失败）", message + "\n\n" + details)

    def show_history(self) -> None:
        dialog = HistoryDialog(BACKUP_ROOT, self)
        dialog.exec()
        if dialog.backup_changed:
            for card in self.cards:
                self.refresh_card_backup_metadata(card)

    def show_game_history(self, app_id: int) -> None:
        card = next((item for item in self.cards if item.app_id == app_id), None)
        if card is None:
            return
        dialog = HistoryDialog(
            BACKUP_ROOT,
            self,
            game_directory=card.backup_directory,
            game_name=card.game_name,
        )
        dialog.exec()
        if dialog.backup_changed or dialog.sources_restored:
            self.refresh_card_backup_metadata(card)

    def refresh_card_backup_metadata(self, card: GameCard) -> None:
        try:
            metadata = backup_metadata(card.game, BACKUP_ROOT)
            card.set_change_state("unchecked", metadata.get("last_backup_at"))
        except OSError:
            card.set_change_state("unchecked", None)

    def operation_failed(self, title: str, message: str) -> None:
        self.set_busy(False)
        self.status.setText(f"{title}：{message}")
        QMessageBox.critical(self, title, message)

    def reveal_path(self, value: str) -> None:
        path = Path(value)
        if not path.exists():
            QMessageBox.warning(self, "路径不存在", f"该路径当前不存在：\n{path}")
            return
        try:
            if os.name == "nt":
                if path.is_file():
                    subprocess.Popen(["explorer.exe", "/select,", str(path)])
                else:
                    subprocess.Popen(["explorer.exe", str(path)])
            else:
                target = path if path.is_dir() else path.parent
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except OSError as error:
            QMessageBox.critical(self, "无法打开路径", str(error))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Steam Saves Backup")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = SteamBackupWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
