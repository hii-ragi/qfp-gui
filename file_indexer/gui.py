"""Tkinter を使った簡易 GUI を提供します。"""

import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Mapping, cast

from file_indexer.config import DEFAULT_BATCH_SIZE, DEFAULT_DB, DEFAULT_WORKERS, load_json, write_json
from file_indexer.filesystem import iter_directory_entries
from file_indexer.indexing import IndexStats, build_excluded_paths, index_folder
from file_indexer.search import search_files, show_stats

import webbrowser


GUI_CONFIG_JSON = Path.cwd() / "qfp-gui.json"


class SettingsWindow(tk.Toplevel):
    """設定ウィンドウを表示するクラスです。"""

    def __init__(self, parent: tk.Tk, folder_var: tk.StringVar, db_var: tk.StringVar, 
                 workers_var: tk.IntVar, batch_size_var: tk.IntVar, max_text_bytes_var: tk.IntVar,
                 on_index_start: Callable[[], None],
                 on_cancel_index: Callable[[], None]) -> None:
        super().__init__(parent)
        self.transient(parent)
        self.title("Index Settings")
        self.geometry("600x350")
        self.resizable(False, False)
        
        self.folder_var = folder_var
        self.db_var = db_var
        self.workers_var = workers_var
        self.batch_size_var = batch_size_var
        self.max_text_bytes_var = max_text_bytes_var
        self.on_index_start = on_index_start
        self.on_cancel_index = on_cancel_index
        self.parent = parent
        self.index_button: ttk.Button | None = None
        self.progress_text_var = tk.StringVar(value="0 / 0, remaining 0")
        
        self._build_widgets()
        self.grab_set()
        self.focus_set()
        
        # ウィンドウが閉じられる時の処理
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _build_widgets(self) -> None:
        """画面部品を組み立てます。"""
        self.columnconfigure(0, weight=1)
        
        # Target Folder
        FolderEntry = ttk.Frame(self)
        FolderEntry.grid(row=0, column=0, columnspan=5, sticky="ew", padx=12, pady=(12, 6))
        FolderEntry.columnconfigure(1, weight=1)
        ttk.Label(FolderEntry, text="Target Folder", width=12).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        ttk.Entry(FolderEntry, textvariable=self.folder_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=12, pady=(12, 6))
        ttk.Button(FolderEntry, text="Browse", command=self.choose_folder).grid(row=0, column=4, padx=(0, 12), pady=(12, 6))

        # Database
        DatabaseEntry = ttk.Frame(self)
        DatabaseEntry.grid(row=1, column=0, columnspan=5, sticky="ew", padx=12, pady=(12, 6))
        DatabaseEntry.columnconfigure(1, weight=1)
        ttk.Label(DatabaseEntry, text="Database File", width=12).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        ttk.Entry(DatabaseEntry, textvariable=self.db_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=12, pady=(12, 6))
        ttk.Button(DatabaseEntry, text="Browse", command=self.choose_db).grid(row=0, column=4, padx=(0, 12), pady=(12, 6))

        # Hash workers and Max text bytes
        options_frame = ttk.Frame(self)
        options_frame.grid(row=2, column=0, columnspan=5, sticky="ew", padx=12, pady=12)
        options_frame.columnconfigure(1, weight=0)
        options_frame.columnconfigure(3, weight=1)
        
        ttk.Label(options_frame, text="Hash workers", width=12).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options_frame, from_=1, to=32, width=6, textvariable=self.workers_var).grid(row=0, column=1, padx=(6, 18))

        ttk.Label(options_frame, text="Batch size", width=10).grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options_frame, from_=1, to=10000, width=8, textvariable=self.batch_size_var).grid(row=0, column=3, padx=(6, 18), sticky="w")
        
        ttk.Label(options_frame, text="Max text bytes", width=12).grid(row=0, column=4, sticky="w")
        ttk.Spinbox(options_frame, from_=1024, to=100 * 1024 * 1024, increment=1024, width=12, textvariable=self.max_text_bytes_var).grid(row=0, column=5, padx=(6, 18), sticky="w")

        # Progressbar
        progress_frame = ttk.LabelFrame(self, text="Index")
        progress_frame.grid(row=3, column=0, columnspan=5, sticky="nsew", padx=12, pady=8)
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.rowconfigure(1, weight=1)

        progress_control = ttk.Frame(progress_frame)
        progress_control.grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 8))
        progress_control.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_control, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=0, column=0, columnspan=3, sticky="ew", padx=12, pady=8)
        ttk.Label(progress_control, textvariable=self.progress_text_var, width=28, anchor="e").grid(row=1, column=0, padx=(10, 0))
        # start_index メソッドを呼び出すボタンを作成し、インデックス作成を開始します。
        self.index_button = ttk.Button(progress_control, text="Start Index", command=self.start_index)
        self.index_button.grid(row=1, column=1, sticky="w")
        # Cancel ボタンを作成し、インデックス作成をキャンセルします。
        self.cancel_button = ttk.Button(progress_control, text="Cancel", command=self.on_cancel_index, state="disabled")
        self.cancel_button.grid(row=1, column=2, padx=(10, 0))
        

        ## Button frame
        button_frame = ttk.Frame(self)
        button_frame.grid(row=4, column=0, columnspan=5, sticky="ew", padx=12, pady=(0, 12))
        button_frame.columnconfigure(0, weight=1)
        # Close ボタンを作成し、ウィンドウを閉じます。
        ttk.Button(button_frame, text="Close", command=self.on_closing).grid(row=5, column=1, sticky="w")

        # GitHub link
        url = "https://github.com/hii-ragi/qfp-gui"
        github_link = ttk.Frame(self)
        github_style = ttk.Style()
        github_style.configure("Link.TLabel", foreground="blue")
        github_label = ttk.Label(github_link,text="Go to GitHub",style="Link.TLabel",cursor="hand2")
        github_label.grid(row=0, column=0, sticky="w", padx=12, pady=(12,0))
        github_label.bind("<Button-1>", lambda e: webbrowser.open(url))
        github_link.grid(row=5, column=1, sticky="w")


    @staticmethod
    def _normalize_path(raw_value: str) -> Path:
        """入力文字列を OS 標準の絶対パスへ正規化します。"""
        value = raw_value.strip()
        if not value:
            return Path.cwd().resolve()
        return Path(value).expanduser().resolve(strict=False)

    def choose_folder(self) -> None:
        """インデックス対象フォルダを選択します。"""
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.cwd()))
        if not folder:
            return

        folder_path = self._normalize_path(folder)
        if folder_path.exists() and folder_path.is_file():
            messagebox.showerror("QFP", f"Select a folder, not a file:\n{folder_path}")
            return

        self.folder_var.set(str(folder_path))

    def choose_db(self) -> None:
        """SQLite DB ファイルを選択します。存在しないパスも指定できます。"""
        db_path = filedialog.asksaveasfilename(
            initialfile=Path(self.db_var.get()).name or DEFAULT_DB,
            defaultextension=".sqlite3",
            filetypes=[("SQLite database", "*.sqlite3 *.db"), ("All files", "*.*")],
        )
        if db_path:
            resolved = self._normalize_path(db_path)
            self.db_var.set(str(resolved))

    def start_index(self) -> None:
        """インデックス作成を開始します。"""
        self.on_index_start()

    def set_index_button_state(self, state: str) -> None:
        """インデックスボタンの状態を設定します。"""
        if self.index_button:
            self.index_button.configure(state=state)

    def set_progress(self, maximum: int, value: int, text: str) -> None:
        """進捗表示を更新します。"""
        self.progress.configure(maximum=maximum, value=value)
        self.progress_text_var.set(text)

    def set_cancel_button_state(self, state: str) -> None:
        """Cancel ボタンの状態を設定します。"""
        self.cancel_button.configure(state=state)

    def on_closing(self) -> None:
        """ウィンドウを閉じます。"""
        self.grab_release()
        self.destroy()


class QfpApp(tk.Tk):
    """フォルダのインデックス作成と検索を行う簡易 UI です。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("QFP")
        self.geometry("900x620")
        self.minsize(760, 520)

        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.index_thread: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        config = self._load_gui_config()
        workers = cast(int, config["workers"])
        batch_size = cast(int, config["batch_size"])
        max_text_bytes = cast(int, config["max_text_bytes"])
        self.folder_var = tk.StringVar(value=str(config["folder"]))
        self.db_var = tk.StringVar(value=str(config["database"]))
        self.workers_var = tk.IntVar(value=workers)
        self.batch_size_var = tk.IntVar(value=batch_size)
        self.max_text_bytes_var = tk.IntVar(value=max_text_bytes)
        self.query_var = tk.StringVar()
        self.limit_var = tk.IntVar(value=20)
        self.status_var = tk.StringVar(value="Ready")
        self.browser_path_var = tk.StringVar(value=str(Path.cwd()))
        self.search_placeholder = "Search word..."
        self.file_sort_column = "name"
        self.file_sort_reverse = False
        self.file_columns = (
            "id", "root", "path", "relative_path", "name", "extension",
            "size", "modified_at", "mime_type", "sha256", "indexed_at",
        )
        self.file_column_labels = {
            "id": "ID", "root": "Root", "path": "Path", "relative_path": "Relative path",
            "name": "Name", "extension": "Extension", "size": "Size",
            "modified_at": "Modified", "mime_type": "MIME type", "sha256": "SHA-256",
            "indexed_at": "Indexed",
        }
        configured_columns = cast(list[str], config["visible_columns"])
        self.visible_file_columns = set(configured_columns) & set(self.file_columns)
        if not self.visible_file_columns:
            self.visible_file_columns = {"name", "extension", "size", "modified_at"}

        self._build_widgets()
        self._poll_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_closing(self) -> None:
        """終了前に現在のGUI設定を保存します。"""
        self._save_gui_config()
        self.destroy()

    @staticmethod
    def _load_gui_config() -> dict[str, object]:
        """GUI設定を読み込み、なければ既定値で作成します。"""
        defaults: dict[str, object] = {
            "folder": str(Path.cwd()),
            "database": str((Path.cwd() / DEFAULT_DB).resolve()),
            "workers": DEFAULT_WORKERS,
            "batch_size": DEFAULT_BATCH_SIZE,
            "max_text_bytes": 1024 * 1024,
            "visible_columns": ["name", "extension", "size", "modified_at"],
        }
        data = load_json(GUI_CONFIG_JSON, defaults)
        merged = {**defaults, **data} if isinstance(data, dict) else defaults
        merged["workers"] = int(cast(int, merged["workers"]))
        merged["batch_size"] = int(cast(int, merged["batch_size"]))
        merged["max_text_bytes"] = int(cast(int, merged["max_text_bytes"]))
        merged["visible_columns"] = cast(list[str], merged["visible_columns"])
        return merged

    def _save_gui_config(self) -> None:
        """現在のGUI設定をJSONへ保存します。"""
        write_json(
            GUI_CONFIG_JSON,
            {
                "folder": self.folder_var.get(),
                "database": self.db_var.get(),
                "workers": self.workers_var.get(),
                "batch_size": self.batch_size_var.get(),
                "max_text_bytes": self.max_text_bytes_var.get(),
                "visible_columns": [
                    column for column in self.file_columns if column in self.visible_file_columns
                ],
            },
        )

    def _build_widgets(self) -> None:
        """画面部品を組み立てます。"""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=8)
        self.main_notebook = notebook

        self._build_file_browser(notebook)
        self._build_index_tab(notebook)

        log_frame = ttk.Frame(notebook)
        notebook.add(log_frame, text="Log")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.output = ScrolledText(log_frame, wrap="word", height=18)
        self.output.grid(row=0, column=0, sticky="nsew", padx=8, pady=(0, 8))
        ttk.Button(log_frame, text="Show Stats", command=self.show_stats).grid(
            row=1, column=0, sticky="w", padx=8, pady=(0, 8)
        )
        self.main_notebook.select(self.file_browser_tab)

        bottom_frame = ttk.Frame(self)
        bottom_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        bottom_frame.columnconfigure(0, weight=1)
        ttk.Label(bottom_frame, textvariable=self.status_var, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self._add_github_link(bottom_frame, 0)

    def _add_github_link(self, parent: ttk.Frame, row: int) -> None:
        """各タブの右下にGitHubリンクを配置します。"""
        link_frame = ttk.Frame(parent)
        link_frame.grid(row=row, column=0, sticky="e", padx=8, pady=(0, 8))
        link_style = ttk.Style()
        link_style.configure("Link.TLabel", foreground="blue")
        github_label = ttk.Label(link_frame, text="Go to GitHub", style="Link.TLabel", cursor="hand2")
        github_label.pack()
        github_label.bind("<Button-1>", lambda _event: webbrowser.open("https://github.com/hii-ragi/qfp-gui"))

    def _build_index_tab(self, notebook: ttk.Notebook) -> None:
        """インデックス設定と進捗表示をIndexタブに組み立てます。"""
        index_frame = ttk.Frame(notebook)
        notebook.add(index_frame, text="Index")
        self.index_tab = index_frame
        index_frame.columnconfigure(0, weight=1)
        index_frame.rowconfigure(4, weight=1)

        folder_entry = ttk.Frame(index_frame)
        folder_entry.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        folder_entry.columnconfigure(1, weight=1)
        ttk.Label(folder_entry, text="Target Folder", width=14).grid(row=0, column=0, sticky="w")
        ttk.Entry(folder_entry, textvariable=self.folder_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(folder_entry, text="Browse", command=self.choose_folder).grid(row=0, column=2)

        database_entry = ttk.Frame(index_frame)
        database_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        database_entry.columnconfigure(1, weight=1)
        ttk.Label(database_entry, text="Database File", width=14).grid(row=0, column=0, sticky="w")
        ttk.Entry(database_entry, textvariable=self.db_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(database_entry, text="Browse", command=self.choose_db).grid(row=0, column=2)

        options = ttk.Frame(index_frame)
        options.grid(row=2, column=0, sticky="w", padx=12, pady=12)
        ttk.Label(options, text="Hash workers").grid(row=0, column=0, padx=(0, 6))
        ttk.Spinbox(options, from_=1, to=32, width=6, textvariable=self.workers_var).grid(row=0, column=1, padx=(0, 18))
        ttk.Label(options, text="Batch size").grid(row=0, column=2, padx=(0, 6))
        ttk.Spinbox(options, from_=1, to=10000, width=8, textvariable=self.batch_size_var).grid(row=0, column=3, padx=(0, 18))
        ttk.Label(options, text="Max text bytes").grid(row=0, column=4, padx=(0, 6))
        ttk.Spinbox(options, from_=1024, to=100 * 1024 * 1024, increment=1024, width=12, textvariable=self.max_text_bytes_var).grid(row=0, column=5)

        progress_frame = ttk.LabelFrame(index_frame, text="Index progress")
        progress_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=8)
        progress_frame.columnconfigure(0, weight=1)
        self.index_progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100, value=0)
        self.index_progress.grid(row=0, column=0, columnspan=3, sticky="ew", padx=12, pady=12)
        self.index_progress_text_var = tk.StringVar(value="0 / 0, remaining 0")
        ttk.Label(progress_frame, textvariable=self.index_progress_text_var, width=28, anchor="e").grid(row=1, column=0, padx=12, pady=(0, 12))
        self.index_button = ttk.Button(progress_frame, text="Start Index", command=self.start_index)
        self.index_button.grid(row=1, column=1, pady=(0, 12))
        self.cancel_button = ttk.Button(progress_frame, text="Cancel", command=self.cancel_index, state="disabled")
        self.cancel_button.grid(row=1, column=2, padx=(10, 12), pady=(0, 12))

    def _build_file_browser(self, notebook: ttk.Notebook) -> None:
        """ファイルマネージャー風の一覧画面を組み立てます。"""
        browser_frame = ttk.Frame(notebook)
        notebook.add(browser_frame, text="Files")
        self.file_browser_tab = browser_frame
        browser_frame.columnconfigure(0, weight=1)
        browser_frame.rowconfigure(2, weight=1)

        browser_controls = ttk.Frame(browser_frame)
        browser_controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        browser_controls.columnconfigure(1, weight=1)
        ttk.Button(browser_controls, text="Up", command=self.go_to_parent).grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(browser_controls, textvariable=self.browser_path_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(browser_controls, text="Go", command=self.go_to_browser_path).grid(row=0, column=2, padx=6)
        ttk.Button(browser_controls, text="Refresh", command=self.refresh_file_browser).grid(row=0, column=3)

        search_controls = ttk.Frame(browser_frame)
        search_controls.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        search_controls.columnconfigure(0, weight=1)
        search_entry = ttk.Entry(search_controls, textvariable=self.query_var)
        search_entry.grid(row=0, column=0, sticky="ew")
        self._set_search_placeholder(search_entry)
        search_entry.bind("<Return>", lambda _event: self.run_search())
        ttk.Label(search_controls, text="Limit").grid(row=0, column=1, padx=(8, 4))
        ttk.Spinbox(search_controls, from_=1, to=200, width=6, textvariable=self.limit_var).grid(row=0, column=2)
        ttk.Button(search_controls, text="Search", command=self.run_search).grid(row=0, column=3, padx=(8, 0))

        tree_frame = ttk.Frame(browser_frame)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.file_tree = ttk.Treeview(
            tree_frame,
            columns=self.file_columns,
            show="headings",
            selectmode="browse",
        )
        for column in self.file_columns:
            self.file_tree.heading(
                column,
                text=self.file_column_labels[column],
                command=lambda selected=column: self.sort_file_tree(selected),
            )
            self.file_tree.column(column, width=110, anchor="w")
        self.file_tree.column("name", width=260)
        self.file_tree.column("path", width=360)
        self.file_tree.column("relative_path", width=260)
        self.file_tree.column("size", width=110, anchor="e")
        self.file_tree.tag_configure("stripe_a", background="#ffffff")
        self.file_tree.tag_configure("stripe_b", background="#f1f5f9")
        ttk.Style().configure("FileBrowser.Treeview", rowheight=26)
        self.file_tree.configure(style="FileBrowser.Treeview")
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.file_tree.yview)
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.file_tree.xview)
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.file_tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.file_tree.bind("<Double-1>", self.open_file_tree_item)
        self.file_tree.bind("<Button-3>", self.open_file_tree_context_menu)
        self.file_column_menu = tk.Menu(self, tearoff=False)
        self.file_column_vars = {
            column: tk.BooleanVar(value=column in self.visible_file_columns)
            for column in self.file_columns
        }
        for column in self.file_columns:
            self.file_column_menu.add_checkbutton(
                label=self.file_column_labels[column],
                variable=self.file_column_vars[column],
                command=lambda selected=column: self.toggle_file_column(selected),
            )
        self.file_tree.configure(
            displaycolumns=tuple(
                column for column in self.file_columns if column in self.visible_file_columns
            )
        )
        self._update_sort_headings()
        self.refresh_file_browser()

    def _set_search_placeholder(self, entry: ttk.Entry) -> None:
        """検索欄が空のとき、入力欄の用途を表示します。"""
        entry.insert(0, self.search_placeholder)
        entry.configure(foreground="#64748b")
        entry.bind(
            "<FocusIn>",
            lambda _event: self._clear_search_placeholder(entry),
            add="+",
        )
        entry.bind(
            "<FocusOut>",
            lambda _event: self._restore_search_placeholder(entry),
            add="+",
        )

    def _clear_search_placeholder(self, entry: ttk.Entry) -> None:
        """検索欄にフォーカスしたときプレースホルダーを消します。"""
        if self.query_var.get() == self.search_placeholder:
            self.query_var.set("")
            entry.configure(foreground="")

    def _restore_search_placeholder(self, entry: ttk.Entry) -> None:
        """検索語が空のときプレースホルダーを戻します。"""
        if not self.query_var.get().strip():
            self.query_var.set(self.search_placeholder)
            entry.configure(foreground="#64748b")

    def refresh_file_browser(self) -> None:
        """現在のフォルダ直下を一覧表示します。"""
        current = SettingsWindow._normalize_path(self.browser_path_var.get())
        if not current.is_dir():
            messagebox.showerror("QFP", f"Folder not found:\n{current}")
            return

        self.browser_path_var.set(str(current))
        self._clear_file_tree()

        excluded_paths = build_excluded_paths(SettingsWindow._normalize_path(self.db_var.get()))
        for path in iter_directory_entries(current, excluded_paths):
            try:
                stat = path.stat()
                is_directory = path.is_dir()
                size = "" if is_directory else f"{stat.st_size:,} bytes"
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size = ""
                modified = ""
                is_directory = path.is_dir()
            values = {
                "path": str(path),
                "relative_path": path.name,
                "name": path.name,
                "extension": "Folder" if is_directory else (path.suffix.lower() or "File"),
                "size": size,
                "modified_at": modified,
            }
            self._insert_file_tree_row(path, values)
        self._refresh_row_stripes()
        self.status_var.set(f"{len(self.file_tree.get_children())} items")

    def _clear_file_tree(self) -> None:
        """ファイル一覧を空にします。"""
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)

    def _insert_file_tree_row(self, path: Path, values: Mapping[str, object]) -> None:
        """ファイル一覧に1行追加します。"""
        self.file_tree.insert(
            "",
            "end",
            iid=str(path),
            tags=("stripe_a" if len(self.file_tree.get_children()) % 2 == 0 else "stripe_b",),
            values=tuple(values.get(column, "") for column in self.file_columns),
        )

    def open_file_tree_context_menu(self, event: tk.Event) -> None:
        """一覧の右クリックで表示するDB列を選べるメニューを開きます。"""
        try:
            self.file_column_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.file_column_menu.grab_release()

    def toggle_file_column(self, column: str) -> None:
        """DB列の表示・非表示を切り替えます。"""
        selected = self.file_column_vars[column].get()
        if not selected and len(self.visible_file_columns) == 1:
            self.file_column_vars[column].set(True)
            return
        if selected:
            self.visible_file_columns.add(column)
        else:
            self.visible_file_columns.discard(column)
        self.file_tree.configure(displaycolumns=tuple(column for column in self.file_columns if column in self.visible_file_columns))
        self._save_gui_config()

    def _refresh_row_stripes(self) -> None:
        """一覧の表示順に合わせて行の背景色を交互に設定します。"""
        for position, item in enumerate(self.file_tree.get_children()):
            self.file_tree.item(item, tags=("stripe_a" if position % 2 == 0 else "stripe_b",))

    def sort_file_tree(self, column: str) -> None:
        """クリックされた列を昇順・降順で切り替えて並べ替えます。"""
        if column == self.file_sort_column:
            self.file_sort_reverse = not self.file_sort_reverse
        else:
            self.file_sort_column = column
            self.file_sort_reverse = False

        items = list(self.file_tree.get_children())
        if column in {"id", "size"}:
            key = lambda item: self._tree_number(self.file_tree.set(item, column))
        else:
            key = lambda item: self.file_tree.set(item, column).casefold()
        items.sort(key=key, reverse=self.file_sort_reverse)
        for position, item in enumerate(items):
            self.file_tree.move(item, "", position)
        self._refresh_row_stripes()
        self._update_sort_headings()

    def _update_sort_headings(self) -> None:
        """現在のソート列に昇順・降順の矢印を表示します。"""
        labels = self.file_column_labels
        arrow = "▼" if self.file_sort_reverse else "▲"
        for column, label in labels.items():
            text = f"{label} {arrow}" if column == self.file_sort_column else label
            self.file_tree.heading(column, text=text)

    @staticmethod
    def _tree_number(value: str) -> int:
        """数値列の表示値を数値へ変換します。"""
        try:
            return int(value.split()[0].replace(",", ""))
        except (IndexError, ValueError):
            return -1

    def go_to_parent(self) -> None:
        """親フォルダへ移動します。"""
        current = SettingsWindow._normalize_path(self.browser_path_var.get())
        self.browser_path_var.set(str(current.parent))
        self.refresh_file_browser()

    def go_to_browser_path(self) -> None:
        """入力されたフォルダへ移動します。"""
        self.refresh_file_browser()

    def open_file_tree_item(self, _event: tk.Event) -> None:
        """選択したフォルダへ移動するか、ファイルを既定アプリで開きます。"""
        if self.file_tree.identify_region(_event.x, _event.y) != "cell":
            return
        selection = self.file_tree.selection()
        if not selection:
            return
        path = Path(selection[0])
        if path.is_dir():
            self.browser_path_var.set(str(path))
            self.refresh_file_browser()
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
        except OSError as exc:
            messagebox.showerror("QFP", f"Could not open file:\n{path}\n\n{exc}")

    def open_settings(self) -> None:
        """Indexタブを表示します。"""
        self.main_notebook.select(self.index_tab)

    def choose_folder(self) -> None:
        """インデックス対象フォルダを選択します。"""
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.cwd()))
        if folder:
            self.folder_var.set(str(SettingsWindow._normalize_path(folder)))

    def choose_db(self) -> None:
        """SQLite DBファイルを選択します。"""
        db_path = filedialog.asksaveasfilename(
            initialfile=Path(self.db_var.get()).name or DEFAULT_DB,
            defaultextension=".sqlite3",
            filetypes=[("SQLite database", "*.sqlite3 *.db"), ("All files", "*.*")],
        )
        if db_path:
            self.db_var.set(str(SettingsWindow._normalize_path(db_path)))

    def start_index(self) -> None:
        """バックグラウンドでインデックス作成を開始します。"""
        if self.index_thread and self.index_thread.is_alive():
            messagebox.showinfo("QFP", "Indexing is already running.")
            return

        folder_raw = self.folder_var.get()
        db_raw = self.db_var.get()

        folder = SettingsWindow._normalize_path(folder_raw)
        db_path = SettingsWindow._normalize_path(db_raw)

        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("QFP", f"Invalid folder path:\n{folder}")
            self.folder_var.set(str(folder))
            return
        if db_path.exists() and db_path.is_dir():
            messagebox.showerror("QFP", f"Database path must be a file, not a folder:\n{db_path}")
            self.db_var.set(str(db_path))
            return

        self.folder_var.set(str(folder))
        self.db_var.set(str(db_path))
        self.browser_path_var.set(str(folder))
        self._save_gui_config()
        self.refresh_file_browser()

        workers = max(1, self.workers_var.get())
        batch_size = max(1, self.batch_size_var.get())
        max_text_bytes = max(0, self.max_text_bytes_var.get())
        self.cancel_event = threading.Event()
        self.cancel_button.configure(state="normal")
        self.index_button.configure(state="disabled")
        self.index_progress.configure(maximum=100, value=0)
        self.index_progress_text_var.set("0 / 0, remaining 0")
        self.status_var.set("Indexing...")
        self._append_output("Index started.\n")

        self.index_thread = threading.Thread(
            target=self._index_worker,
            args=(db_path, folder, max_text_bytes, workers, batch_size),
            daemon=True,
        )
        self.index_thread.start()

    def _index_worker(self, db_path: Path, folder: Path, max_text_bytes: int, workers: int, batch_size: int) -> None:
        """GUI を固めないように別スレッドでインデックス処理を行います。"""
        try:
            stats = index_folder(
                db_path=db_path,
                folder=folder,
                max_text_bytes=max_text_bytes,
                show_progress=False,
                log_enabled=True,
                logger=lambda message: self.message_queue.put(("log", message)),
                workers=workers,
                batch_size=batch_size,
                cancel_event=self.cancel_event,
                progress_callback=lambda current, total, label: self.message_queue.put(
                    ("progress", (current, total, label))
                ),
            )
            if self.cancel_event and self.cancel_event.is_set():
                self.message_queue.put(("cancelled", stats))
            else:
                self.message_queue.put(("done", stats))
        except Exception as exc:
            self.message_queue.put(("error", exc))

    def cancel_index(self) -> None:
        """実行中のインデックス作成を停止要求します。"""
        if self.index_thread and self.index_thread.is_alive() and self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("Cancelling...")
            self._append_output("Index cancellation requested.\n")

    def _poll_queue(self) -> None:
        """バックグラウンド処理から届いたメッセージを UI に反映します。"""
        while True:
            try:
                kind, payload = self.message_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_output(f"{payload}\n")
            elif kind == "progress":
                current, total, label = cast(tuple[int, int, str], payload)
                maximum = max(total, 1)
                remaining = max(total - current, 0)
                self.index_progress.configure(maximum=maximum, value=current)
                self.index_progress_text_var.set(f"{current} / {total}, remaining {remaining}")
                self.status_var.set(f"Indexing... {current}/{total} {label}")
            elif kind == "done":
                stats = cast(IndexStats, payload)
                self.index_progress.configure(value=self.index_progress["maximum"])
                self.index_progress_text_var.set(f"{stats.scanned} / {stats.scanned}, remaining 0")
                self.cancel_button.configure(state="disabled")
                self.index_button.configure(state="normal")
                self.status_var.set("Index complete")
                self.refresh_file_browser()
                self._append_output(
                    "Index complete: "
                    f"scanned {stats.scanned}, stored {stats.stored}, "
                    f"skipped {stats.skipped}, failed {stats.failed}\n"
                )
            elif kind == "cancelled":
                self.cancel_button.configure(state="disabled")
                self.index_button.configure(state="normal")
                self.status_var.set("Index cancelled")
                stats = cast(IndexStats, payload)
                self.index_progress.configure(value=stats.scanned)
                self.index_progress_text_var.set(
                    f"{stats.scanned} / {self.index_progress['maximum']}, remaining 0"
                )
                self._append_output(
                    "Index cancelled: "
                    f"scanned {stats.scanned}, stored {stats.stored}, "
                    f"skipped {stats.skipped}, failed {stats.failed}\n"
                )
            elif kind == "error":
                self.index_progress.configure(maximum=100, value=0)
                self.index_progress_text_var.set("0 / 0, remaining 0")
                self.cancel_button.configure(state="disabled")
                self.index_button.configure(state="normal")
                self.status_var.set("Error")
                messagebox.showerror("QFP", str(payload))

        self.after(100, self._poll_queue)

    def run_search(self) -> None:
        """検索を実行してファイルマネージャー一覧に結果を表示します。"""
        query = self.query_var.get().strip()
        if not query or query == self.search_placeholder:
            messagebox.showinfo("QFP", "Enter a search query.")
            return

        try:
            rows = search_files(Path(self.db_var.get()), query, self.limit_var.get())
        except Exception as exc:
            messagebox.showerror("QFP", str(exc))
            return

        self.main_notebook.select(self.file_browser_tab)
        self._clear_file_tree()
        if not rows:
            self.status_var.set("No files found")
            return

        for row in rows:
            path = Path(row["path"])
            values = dict(row)
            values["size"] = f"{row['size']:,} bytes"
            self._insert_file_tree_row(path, values)
        self._refresh_row_stripes()
        self._update_sort_headings()
        self.status_var.set(f"Search: {len(rows)} items")

    def show_stats(self) -> None:
        """DB の登録件数などを表示します。"""
        try:
            stats = show_stats(Path(self.db_var.get()))
        except Exception as exc:
            messagebox.showerror("QFP", str(exc))
            return

        self._append_output(
            f"DB: {self.db_var.get()}\n"
            f"files: {stats['file_count']}\n"
            f"bytes: {stats['total_bytes']}\n"
            f"last_indexed_at: {stats['last_indexed_at']}\n\n"
        )

    def _append_output(self, text: str) -> None:
        """ログ欄にテキストを追記します。"""
        self.output.insert("end", text)
        self.output.see("end")


def main() -> int:
    """GUI を起動します。"""
    app = QfpApp()
    app.mainloop()
    return 0
