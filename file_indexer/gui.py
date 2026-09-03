"""PySide6を使ったGUIを提供します"""

import os
import queue
import sys
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, cast

from PySide6.QtCore import QFileSystemWatcher, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QGroupBox, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QCheckBox,
)

from file_indexer.config import DEFAULT_BATCH_SIZE, DEFAULT_DB, DEFAULT_WORKERS, load_json, write_json
from file_indexer.filesystem import iter_directory_entries
from file_indexer.indexing import IndexStats, build_excluded_paths, index_folder
from file_indexer.search import search_files, show_stats


GUI_CONFIG_JSON = Path.cwd() / "qfp-gui.json"


class FileTreeItem(QTreeWidgetItem):
    """ファイル一覧のID列を数値としてソートする項目"""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        if tree is None:
            return False

        column = tree.sortColumn()
        left_value = self.text(column)
        right_value = other.text(column)
        if column == 0:
            try:
                return int(left_value) < int(right_value)
            except ValueError:
                pass
        return left_value.casefold() < right_value.casefold()


class FileTreeWidget(QTreeWidget):
    """マウスサイドボタンでフォルダー移動できるファイル一覧"""

    def __init__(self, up_callback: Callable[[], None], down_callback: Callable[[], None]) -> None:
        super().__init__()
        self._up_callback = up_callback
        self._down_callback = down_callback

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.XButton1:
            self._up_callback()
            event.accept()
            return
        if event.button() == Qt.MouseButton.XButton2:
            self._down_callback()
            event.accept()
            return
        super().mousePressEvent(event)


class QfpApp(QMainWindow):
    """フォルダのインデックス作成と検索を行うGUI"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("QFP")
        self.resize(1000, 680)
        self.setMinimumSize(820, 560)
        config = self._load_gui_config()
        self.folder = Path(str(config["folder"]))
        self.db_path = Path(str(config["database"]))
        self.workers = int(cast(int, config["workers"]))
        self.batch_size = int(cast(int, config["batch_size"]))
        self.max_text_bytes = int(cast(int, config["max_text_bytes"]))
        self.font_size = int(cast(int, config.get("font_size", 12)))
        self.auto_index = bool(config.get("auto_index", False))
        self.theme_name = str(config.get("theme", "Default"))
        self.accent_color = QColor(str(config.get("accent_color", "#4f8cff")))
        self.background_color = QColor(str(config.get("background_color", "#f5f5f5")))
        self.text_color = QColor(str(config.get("text_color", "#1f1f1f")))
        self.file_columns = ("id", "root", "path", "relative_path", "name", "extension", "size", "modified_at", "mime_type", "sha256", "indexed_at")
        self.column_labels = {"id": "ID", "root": "Root", "path": "Path", "relative_path": "Relative path", "name": "Name", "extension": "Extension", "size": "Size", "modified_at": "Modified", "mime_type": "MIME type", "sha256": "SHA-256", "indexed_at": "Indexed"}
        self._file_icons: dict[str, QIcon] = {}
        configured_columns = cast(list[str], config["visible_columns"])
        self.visible_columns = [column for column in self.file_columns if column in configured_columns] or ["name", "extension", "size", "modified_at"]
        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.index_thread: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        self.auto_index_watcher = QFileSystemWatcher(self)
        self.auto_index_watcher.directoryChanged.connect(self._on_watched_path_changed)
        self.auto_index_watcher.fileChanged.connect(self._on_watched_path_changed)
        self._auto_index_pending = False
        self._build_widgets()
        self._update_auto_index_watches()
        self._poll_queue()

    @staticmethod
    def _info_icon(description: str) -> QLabel:
        info = QLabel("i")
        info.setToolTip(description)
        asset_root = Path(str(getattr(sys, "_MEIPASS", Path.cwd()))) / "file_indexer" / "assets" if getattr(sys, "frozen", False) else Path(__file__).parent / "assets"
        info.setPixmap(QIcon(str(asset_root / "info.svg")).pixmap(16, 16))
        return info

    @staticmethod
    def _info_label(text: str, description: str) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(QLabel(text))
        info = QfpApp._info_icon(description)
        row.addWidget(info)
        return container

    @staticmethod
    def _load_gui_config() -> dict[str, object]:
        defaults: dict[str, object] = {"folder": str(Path.cwd()), "database": str((Path.cwd() / DEFAULT_DB).resolve()), "workers": DEFAULT_WORKERS, "batch_size": DEFAULT_BATCH_SIZE, "max_text_bytes": 1024 * 1024, "visible_columns": ["name", "extension", "size", "modified_at"], "theme": "Default", "accent_color": "#4f8cff", "background_color": "#f5f5f5", "text_color": "#1f1f1f", "font_size": 12, "auto_index": False}
        data = load_json(GUI_CONFIG_JSON, defaults)
        merged = {**defaults, **data} if isinstance(data, dict) else defaults
        merged["workers"] = int(cast(int, merged["workers"]))
        merged["batch_size"] = int(cast(int, merged["batch_size"]))
        merged["max_text_bytes"] = int(cast(int, merged["max_text_bytes"]))
        merged["font_size"] = int(cast(int, merged.get("font_size", 12)))
        merged["visible_columns"] = cast(list[str], merged["visible_columns"])
        merged["theme"] = str(merged.get("theme", "Default"))
        merged["accent_color"] = str(merged.get("accent_color", "#4f8cff"))
        merged["background_color"] = str(merged.get("background_color", "#f5f5f5"))
        merged["text_color"] = str(merged.get("text_color", "#1f1f1f"))
        return merged

    def _save_gui_config(self) -> None:
        write_json(GUI_CONFIG_JSON, {"folder": str(self.folder), "database": str(self.db_path), "workers": self.workers, "batch_size": self.batch_size, "max_text_bytes": self.max_text_bytes, "visible_columns": self.visible_columns, "theme": self.theme_name, "accent_color": self.accent_color.name(), "background_color": self.background_color.name(), "text_color": self.text_color.name(), "font_size": self.font_size, "auto_index": self.auto_index})

    def _build_widgets(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        self.status_label = QLabel("Ready")
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._build_files_tab()
        self._build_index_tab()
        self._build_log_tab()
        self._build_settings_tab()
        self.tabs.setCurrentWidget(self.files_tab)
        self._apply_theme()
        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        github = QLabel('<a href="https://github.com/hii-ragi/qfp-gui">Go to GitHub</a>')
        github.setOpenExternalLinks(True)
        bottom.addWidget(github)
        root.addLayout(bottom)

    def _build_files_tab(self) -> None:
        self.files_tab = QWidget()
        layout = QVBoxLayout(self.files_tab)
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(self.folder))
        up_button = QPushButton("←")
        up_button.clicked.connect(self.go_to_parent)
        go_button = QPushButton("↵")
        go_button.clicked.connect(self.go_to_path)
        refresh_button = QPushButton("↻")
        refresh_button.clicked.connect(self.refresh_file_browser)
        path_row.addWidget(up_button)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(go_button)
        path_row.addWidget(refresh_button)
        layout.addLayout(path_row)
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search word...")
        self.search_edit.returnPressed.connect(self.run_search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(QLabel("Limit"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 200)
        self.limit_spin.setValue(20)
        search_row.addWidget(self.limit_spin)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.run_search)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)
        self.file_tree = FileTreeWidget(self.go_to_parent, self.go_to_selected_folder)
        self.file_tree.setColumnCount(len(self.file_columns))
        self.file_tree.setHeaderLabels([self.column_labels[column] for column in self.file_columns])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setSortingEnabled(True)
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self.open_column_menu)
        self.file_tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.header().customContextMenuRequested.connect(self.open_header_column_menu)
        self.file_tree.itemDoubleClicked.connect(self.open_file_tree_item)
        layout.addWidget(self.file_tree, 1)
        self.tabs.addTab(self.files_tab, "Files")
        self.apply_visible_columns()
        self.refresh_file_browser()

    def _build_index_tab(self) -> None:
        self.index_tab = QWidget()
        layout = QGridLayout(self.index_tab)
        layout.setColumnStretch(1, 1)
        layout.addWidget(self._info_label("Target Folder", "Folder used for manual recursive indexing."), 0, 0)
        self.folder_edit = QLineEdit(str(self.folder))
        layout.addWidget(self.folder_edit, 0, 1)
        browse_folder_button = QPushButton("Browse")
        browse_folder_button.clicked.connect(self.choose_folder)
        layout.addWidget(browse_folder_button, 0, 2)
        layout.addWidget(self._info_label("Database File", "SQLite database file where indexed file metadata is stored."), 1, 0)
        self.db_edit = QLineEdit(str(self.db_path))
        layout.addWidget(self.db_edit, 1, 1)
        browse_db_button = QPushButton("Browse")
        browse_db_button.clicked.connect(self.choose_db)
        layout.addWidget(browse_db_button, 1, 2)
        settings_box = QGroupBox("Index Settings")
        settings_layout = QGridLayout(settings_box)
        settings_layout.setColumnStretch(1, 1)
        self.workers_spin: QSpinBox = QSpinBox()
        self.batch_spin: QSpinBox = QSpinBox()
        self.max_text_spin: QSpinBox = QSpinBox()
        for row, label, value, maximum, attr in ((2, "Hash workers", self.workers, 32, "workers_spin"), (3, "Batch size", self.batch_size, 10000, "batch_spin"), (4, "Max text bytes", self.max_text_bytes, 100 * 1024 * 1024, "max_text_spin")):
            settings_row = row - 2
            descriptions = {
                "Hash workers": "Number of background workers used for hashing and reading files.",
                "Batch size": "Number of prepared files written to the database in one batch.",
                "Max text bytes": "Maximum file size whose text content is read for searching.",
            }
            settings_layout.addWidget(self._info_label(label, descriptions[label]), settings_row, 0)
            spin = getattr(self, attr)
            spin.setRange(1 if row < 4 else 1024, maximum)
            spin.setValue(value)
            if row == 4:
                spin.setSingleStep(1024)
            settings_layout.addWidget(spin, settings_row, 1)
        self.progress = QProgressBar()
        self.progress_text = QLabel("0 / 0, remaining 0")
        settings_layout.addWidget(self.progress, 3, 0, 1, 2)
        settings_layout.addWidget(self.progress_text, 4, 0)
        self.index_button = QPushButton("Start Index")
        self.index_button.clicked.connect(self.start_index)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_index)
        self.cancel_button.setEnabled(False)
        self.index_button.setFixedWidth(120)
        settings_layout.addWidget(self.index_button, 4, 1, alignment=Qt.AlignmentFlag.AlignRight)
        settings_layout.addWidget(self.cancel_button, 4, 2)
        layout.addWidget(settings_box, 2, 0, 1, 3)
        layout.setRowStretch(3, 1)
        self.tabs.addTab(self.index_tab, "Index")

    def _build_log_tab(self) -> None:
        self.log_tab = QWidget()
        layout = QVBoxLayout(self.log_tab)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)
        show_stats_button = QPushButton("Show Stats")
        show_stats_button.clicked.connect(self.show_stats)
        layout.addWidget(show_stats_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(self.log_tab, "Log")

    def _build_settings_tab(self) -> None:
        self.settings_tab = QWidget()
        layout = QGridLayout(self.settings_tab)
        layout.setColumnStretch(1, 1)
        layout.addWidget(self._info_label("Theme", "Select the application's color theme."), 0, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Default", "Dark", "Custom"])
        self.theme_combo.setCurrentText(self.theme_name)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        layout.addWidget(self.theme_combo, 0, 1)
        layout.addWidget(QLabel("Accent color"), 1, 0)
        self.accent_button = QPushButton(self.accent_color.name())
        self.accent_button.clicked.connect(lambda: self._choose_color("accent"))
        layout.addWidget(self.accent_button, 1, 1)
        layout.addWidget(QLabel("Background color"), 2, 0)
        self.background_button = QPushButton(self.background_color.name())
        self.background_button.clicked.connect(lambda: self._choose_color("background"))
        layout.addWidget(self.background_button, 2, 1)
        layout.addWidget(QLabel("Text color"), 3, 0)
        self.text_button = QPushButton(self.text_color.name())
        self.text_button.clicked.connect(lambda: self._choose_color("text"))
        layout.addWidget(self.text_button, 3, 1)
        layout.addWidget(self._info_label("Font size", "Font size used throughout the application."), 4, 0)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(self.font_size)
        self.font_size_spin.valueChanged.connect(self._on_font_size_changed)
        layout.addWidget(self.font_size_spin, 4, 1)
        self.auto_index_check = QCheckBox("Auto index")
        self.auto_index_check.setToolTip("Automatically index files in the currently viewed folder when its contents change.")
        self.auto_index_check.setChecked(self.auto_index)
        self.auto_index_check.toggled.connect(self._on_auto_index_changed)
        auto_index_row = QHBoxLayout()
        auto_index_row.setContentsMargins(0, 0, 0, 0)
        auto_index_row.addWidget(self.auto_index_check)
        auto_index_row.addWidget(self._info_icon("Automatically index files in the currently viewed folder when its contents change."))
        auto_index_row.addStretch(1)
        layout.addLayout(auto_index_row, 5, 0, 1, 2)
        layout.setRowStretch(6, 1)
        self.tabs.addTab(self.settings_tab, "Setting")

    def _on_theme_changed(self, theme_name: str) -> None:
        self.theme_name = theme_name
        if theme_name == "Default":
            self.accent_color = QColor("#A9A9A9")
            self.background_color = QColor("#f5f5f5")
            self.text_color = QColor("#1f1f1f")
        elif theme_name == "Dark":
            self.accent_color = QColor("#2e63be")
            self.background_color = QColor("#1f1f22")
            self.text_color = QColor("#e6e6e6")
        self._apply_theme()

    def _choose_color(self, key: str) -> None:
        current_color = getattr(self, f"{key}_color")
        chosen = QColorDialog.getColor(current_color, self, f"Select {key} color")
        if not chosen.isValid():
            return
        setattr(self, f"{key}_color", chosen)
        getattr(self, f"{key}_button").setText(chosen.name())
        self.theme_name = "Custom"
        self.theme_combo.setCurrentText("Custom")
        self._apply_theme()

    def _on_font_size_changed(self, font_size: int) -> None:
        self.font_size = font_size
        self._apply_theme()

    def _on_auto_index_changed(self, enabled: bool) -> None:
        self.auto_index = enabled
        self._update_auto_index_watches()
        self._save_gui_config()
        self._append_output(f"Auto index {'enabled' if enabled else 'disabled'}.\n")
        if enabled:
            self._start_auto_index(self.folder)

    def _start_auto_index(self, folder: Path) -> None:
        if self.index_thread and self.index_thread.is_alive():
            self._append_output("Auto index skipped: indexing is already running.\n")
            return
        self._append_output("Auto index started.\n")
        self._start_index_for_folder(folder, recursive=False)

    def _update_auto_index_watches(self) -> None:
        directories = self.auto_index_watcher.directories()
        files = self.auto_index_watcher.files()
        if directories:
            self.auto_index_watcher.removePaths(directories)
        if files:
            self.auto_index_watcher.removePaths(files)
        if not self.auto_index or not self.folder.is_dir():
            return
        paths = [str(self.folder.resolve())]
        try:
            paths.extend(str(path) for path in self.folder.iterdir() if path.is_file())
        except OSError:
            return
        self.auto_index_watcher.addPaths(paths)

    def _on_watched_path_changed(self, _path: str) -> None:
        if not self.auto_index or self._auto_index_pending:
            return
        self._append_output(f"Auto index change detected: {_path}\n")
        self._auto_index_pending = True
        QTimer.singleShot(300, self._run_scheduled_auto_index)

    def _run_scheduled_auto_index(self) -> None:
        self._auto_index_pending = False
        if not self.auto_index or (self.index_thread and self.index_thread.is_alive()):
            return
        self._append_output("Auto index started.\n")
        self._start_index_for_folder(self.folder, recursive=False)

    def _apply_theme(self) -> None:
        if self.theme_name == "Dark":
            self.accent_color = QColor("#2e63be")
            self.background_color = QColor("#1f1f22")
            self.text_color = QColor("#e6e6e6")
            selection_color = QColor("#3d7bd9")
            hover_color = QColor("#2a3f61")
        elif self.theme_name == "Default":
            self.accent_color = QColor("#A9A9A9")
            self.background_color = QColor("#f5f5f5")
            self.text_color = QColor("#1f1f1f")
            selection_color = QColor("#b8d2ff")
            hover_color = QColor("#e8f1ff")
        elif self.theme_name == "Custom":
            selection_color = self.accent_color
            hover_color = self.accent_color.lighter(160)

        accent = self.accent_color.name()
        bg = self.background_color.name()
        fg = self.text_color.name()
        selection = selection_color.name()
        hover = hover_color.name()
        field_bg = self.background_color.lighter(120).name()
        border = self.accent_color.darker(120).name()

        app_font = QFont(self.font().family())
        app_font.setPointSize(max(1, self.font_size))
        self.setFont(app_font)

        self.setStyleSheet(
            f"""
            QMainWindow {{ background-color: {bg}; color: {fg}; font-size: {self.font_size}px; }}
            QWidget {{ background-color: {bg}; color: {fg}; font-size: {self.font_size}px; }}
            QTabWidget::pane {{ border: 1px solid {border}; background-color: {bg}; }}
            QTabBar::tab {{ background: {field_bg}; color: {fg}; padding: 6px 12px; border: 1px solid {border}; border-top-left-radius: 4px; border-top-right-radius: 4px; border-bottom: none; }}
            QTabBar::tab:selected {{ background: {accent}; color: {fg}; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
            QSpinBox::up-button {{subcontrol-origin: border; subcontrol-position: top right;width: 16px;}}
            QSpinBox::down-button {{subcontrol-origin: border; subcontrol-position: bottom right;width: 16px;}}
            QLineEdit, QSpinBox, QTextEdit, QTreeWidget {{ background-color: {field_bg}; color: {fg}; border: 1px solid {border}; font-size: {self.font_size}px; }}
            QTreeWidget::item:hover {{ background-color: {hover}; color: {fg}; }}
            QTreeWidget::item:selected {{ background-color: {selection}; color: {fg}; }}
            QPushButton {{ background-color: {accent}; color: {fg}; border: 1px solid {border}; border-radius: 4px; padding: 2px 10px; font-size: {self.font_size}px; }}
            QPushButton:disabled {{ background-color: {field_bg}; color: {fg}; opacity: 0.7; border-radius: 4px; }}
            QProgressBar {{ border: 1px solid {border}; background-color: {field_bg}; color: {fg}; }}
            QProgressBar::chunk {{ background-color: {accent}; }}
            QLabel {{ color: {fg}; font-size: {self.font_size}px; }}
            QGroupBox {{ border: 1px solid {border}; margin-top: 10px; color: {fg}; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {fg}; }}
            """
        )
        self.accent_button.setText(self.accent_color.name())
        self.background_button.setText(self.background_color.name())
        self.text_button.setText(self.text_color.name())
        if hasattr(self, "theme_combo"):
            self.theme_combo.setCurrentText(self.theme_name)
        self._save_gui_config()

    def apply_visible_columns(self) -> None:
        for index, column in enumerate(self.file_columns):
            self.file_tree.setColumnHidden(index, column not in self.visible_columns)

    def open_column_menu(self, position) -> None:
        """TreeView本体の右クリックメニューを表示"""
        item = self.file_tree.itemAt(position)
        global_position = self.file_tree.viewport().mapToGlobal(position)
        if item is None:
            return

        menu = QMenu(self)
        open_in_explorer = menu.addAction("Open in Explorer ")
        open_in_explorer.triggered.connect(lambda: self.open_in_explorer(item))
        menu.exec(global_position)

    def open_header_column_menu(self, position) -> None:
        """TreeViewヘッダーの右クリックで表示列メニューを開く"""
        self._show_column_menu(self.file_tree.header().mapToGlobal(position))

    def _show_column_menu(self, global_position) -> None:
        """DB列の表示・非表示メニューを指定位置に表示"""
        menu = QMenu(self)
        self._add_column_actions(menu)
        menu.exec(global_position)

    def _add_column_actions(self, menu: QMenu) -> None:
        """DB列の表示・非表示アクションをメニューへ追加"""
        for column in self.file_columns:
            action = menu.addAction(self.column_labels[column])
            action.setCheckable(True)
            action.setChecked(column in self.visible_columns)
            action.triggered.connect(lambda checked, selected=column: self.toggle_column(selected, checked))

    def open_in_explorer(self, item: QTreeWidgetItem) -> None:
        """選択したファイルまたはフォルダをエクスプローラーで開く"""
        raw_path = str(item.data(0, Qt.ItemDataRole.UserRole))
        normalized_path = os.path.normpath(raw_path)
        path = Path(normalized_path)
        if not path.exists():
            QMessageBox.warning(self, "QFP", f"Path not found:\n{path}")
            return
        try:
            if sys.platform == "win32":
                if path.is_dir():
                    os.startfile(str(path))
                elif self._is_smb_path(normalized_path):
                    os.startfile(str(path.parent))
                else:
                    subprocess.Popen(["explorer.exe", f"/select,{path}"])
            else:
                webbrowser.open(path.parent.as_uri() if path.is_file() else path.as_uri())
        except OSError as exc:
            QMessageBox.warning(self, "QFP", f"Could not open Explorer:\n{path}\n\n{exc}")

    @staticmethod
    def _is_smb_path(path: str) -> bool:
        """UNC形式のネットワークパスか判定"""
        return path.startswith(("\\\\", "//"))

    def toggle_column(self, column: str, visible: bool) -> None:
        if not visible and len(self.visible_columns) == 1:
            return
        if visible and column not in self.visible_columns:
            self.visible_columns.append(column)
        elif not visible and column in self.visible_columns:
            self.visible_columns.remove(column)
        self.apply_visible_columns()
        self._save_gui_config()

    def refresh_file_browser(self) -> None:
        current = Path(self.path_edit.text()).expanduser().resolve()
        if not current.is_dir():
            QMessageBox.warning(self, "QFP", f"Folder not found:\n{current}")
            return
        folder_changed = current != self.folder.resolve()
        self.folder = current
        self.path_edit.setText(str(current))
        self.file_tree.clear()
        for path in iter_directory_entries(current, build_excluded_paths(self.db_path)):
            values = {"path": str(path), "relative_path": path.name, "name": path.name, "extension": "Folder" if path.is_dir() else (path.suffix.lower() or "File")}
            try:
                stat = path.stat()
                values["size"] = "" if path.is_dir() else f"{stat.st_size:,} bytes"
                values["modified_at"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                values["size"] = ""
                values["modified_at"] = ""
            self._insert_file_tree_row(path, values)
        self.status_label.setText(f"{self.file_tree.topLevelItemCount()} items")
        self._update_auto_index_watches()
        if folder_changed and self.auto_index and hasattr(self, "index_button"):
            self._start_auto_index(current)

    def _insert_file_tree_row(self, path: Path, values: Mapping[str, object]) -> None:
        item = FileTreeItem([str(values.get(column, "")) for column in self.file_columns])
        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        item.setIcon(self.file_columns.index("name"), self._icon_for_path(path))
        self.file_tree.addTopLevelItem(item)

    def _icon_for_path(self, path: Path) -> QIcon:
        """パスの種類に応じたSVGアイコンを返す"""
        if path.is_dir():
            icon_name = "folder"
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}:
            icon_name = "image"
        elif path.suffix.lower() in {".zip", ".7z", ".rar", ".tar", ".gz"}:
            icon_name = "archive"
        elif path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            icon_name = "database"
        elif path.suffix.lower() in {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".yml"}:
            icon_name = "code"
        else:
            icon_name = "file"

        if icon_name not in self._file_icons:
            if getattr(sys, "frozen", False):
                asset_root = Path(str(getattr(sys, "_MEIPASS", Path.cwd()))) / "file_indexer" / "assets"
            else:
                asset_root = Path(__file__).parent / "assets"
            self._file_icons[icon_name] = QIcon(str(asset_root / f"{icon_name}.svg"))
        return self._file_icons[icon_name]

    def open_file_tree_item(self, item: QTreeWidgetItem, _column: int) -> None:
        path = Path(str(item.data(0, Qt.ItemDataRole.UserRole)))
        if path.is_dir():
            self.path_edit.setText(str(path))
            self.refresh_file_browser()
            return
        try:
            os.startfile(str(path)) if sys.platform == "win32" else webbrowser.open(path.as_uri())
        except OSError as exc:
            QMessageBox.warning(self, "QFP", f"Could not open file:\n{path}\n\n{exc}")

    def go_to_parent(self) -> None:
        self.path_edit.setText(str(Path(self.path_edit.text()).expanduser().resolve().parent))
        self.refresh_file_browser()

    def go_to_selected_folder(self) -> None:
        """選択中のフォルダーへ移動"""
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            return
        path = Path(str(selected_items[0].data(0, Qt.ItemDataRole.UserRole)))
        if path.is_dir():
            self.path_edit.setText(str(path))
            self.refresh_file_browser()

    def go_to_path(self) -> None:
        self.refresh_file_browser()

    def run_search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            QMessageBox.information(self, "QFP", "Enter a search query.")
            return
        try:
            rows = search_files(self.db_path, query, self.limit_spin.value())
        except Exception as exc:
            QMessageBox.critical(self, "QFP", str(exc))
            return
        self.file_tree.clear()
        for row in rows:
            self._insert_file_tree_row(Path(row["path"]), dict(row))
        self.status_label.setText(f"Search: {len(rows)} items")
        self.tabs.setCurrentWidget(self.files_tab)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder", str(self.folder))
        if folder:
            self.folder_edit.setText(folder)

    def choose_db(self) -> None:
        db_path, _ = QFileDialog.getSaveFileName(self, "Select database", str(self.db_path), "SQLite database (*.sqlite3 *.db);;All files (*.*)")
        if db_path:
            self.db_edit.setText(db_path)

    def start_index(self) -> None:
        if self.index_thread and self.index_thread.is_alive():
            QMessageBox.information(self, "QFP", "Indexing is already running.")
            return
        folder = Path(self.folder_edit.text()).expanduser().resolve()
        db_path = Path(self.db_edit.text()).expanduser().resolve()
        if not folder.is_dir():
            QMessageBox.warning(self, "QFP", f"Invalid folder path:\n{folder}")
            return
        if db_path.exists() and db_path.is_dir():
            QMessageBox.warning(self, "QFP", f"Database path must be a file:\n{db_path}")
            return
        self.folder, self.db_path = folder, db_path
        self.workers, self.batch_size, self.max_text_bytes = self.workers_spin.value(), self.batch_spin.value(), self.max_text_spin.value()
        self._save_gui_config()
        self._start_index_for_folder(folder, recursive=True)

    def _start_index_for_folder(self, folder: Path, recursive: bool) -> None:
        self.folder = folder
        self.path_edit.setText(str(folder))
        self._starting_index = True
        try:
            self.refresh_file_browser()
        finally:
            self._starting_index = False
        self.cancel_event = threading.Event()
        self.index_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self._append_output(f"Index started ({'recursive' if recursive else 'current folder only'}): {folder}\n")
        self.status_label.setText("Indexing...")
        self.index_thread = threading.Thread(target=self._index_worker, args=(folder, recursive), daemon=True)
        self.index_thread.start()

    def _index_worker(self, folder: Path, recursive: bool) -> None:
        try:
            stats = index_folder(self.db_path, folder, self.max_text_bytes, show_progress=False, log_enabled=True, logger=lambda message: self.message_queue.put(("log", message)), workers=self.workers, batch_size=self.batch_size, cancel_event=self.cancel_event, progress_callback=lambda current, total, label: self.message_queue.put(("progress", (current, total, label))), recursive=recursive)
            self.message_queue.put(("cancelled" if self.cancel_event and self.cancel_event.is_set() else "done", stats))
        except Exception as exc:
            self.message_queue.put(("error", exc))

    def cancel_index(self) -> None:
        if self.index_thread and self.index_thread.is_alive() and self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling...")

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, payload = self.message_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_output(f"{payload}\n")
            elif kind == "progress":
                current, total, label = cast(tuple[int, int, str], payload)
                self.progress.setMaximum(max(total, 1))
                self.progress.setValue(current)
                self.progress_text.setText(f"{current} / {total}, remaining {max(total - current, 0)}")
                self.status_label.setText(f"Indexing... {current}/{total} {label}")
            elif kind in {"done", "cancelled"}:
                stats = cast(IndexStats, payload)
                self.index_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.status_label.setText("Index complete" if kind == "done" else "Index cancelled")
                self._append_output(f"Index {'complete' if kind == 'done' else 'cancelled'}: scanned {stats.scanned}, stored {stats.stored}, skipped {stats.skipped}, failed {stats.failed}\n")
            elif kind == "error":
                self.index_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.status_label.setText("Error")
                QMessageBox.critical(self, "QFP", str(payload))
        QTimer.singleShot(100, self._poll_queue)

    def show_stats(self) -> None:
        try:
            stats = show_stats(self.db_path)
        except Exception as exc:
            QMessageBox.critical(self, "QFP", str(exc))
            return
        self._append_output(f"DB: {self.db_path}\nfiles: {stats['file_count']}\nbytes: {stats['total_bytes']}\nlast_indexed_at: {stats['last_indexed_at']}\n")
        self.tabs.setCurrentWidget(self.log_tab)

    def _append_output(self, text: str) -> None:
        self.output.append(text.rstrip("\n"))

    def closeEvent(self, event) -> None:
        self._save_gui_config()
        event.accept()


def main() -> int:
    """GUIを起動します。"""
    app = QApplication(sys.argv)
    window = QfpApp()
    window.show()
    return app.exec()
