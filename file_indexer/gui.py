"""Tkinter を使った簡易 GUI を提供します。"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable, cast

from file_indexer.config import DEFAULT_BATCH_SIZE, DEFAULT_DB, DEFAULT_WORKERS
from file_indexer.indexing import IndexStats, index_folder
from file_indexer.search import search_files, show_stats

import webbrowser


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
        self.settings_window: SettingsWindow | None = None

        self.folder_var = tk.StringVar(value=str(Path.cwd()))
        self.db_var = tk.StringVar(value=str((Path.cwd() / DEFAULT_DB).resolve()))
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        self.batch_size_var = tk.IntVar(value=DEFAULT_BATCH_SIZE)
        self.max_text_bytes_var = tk.IntVar(value=1024 * 1024)
        self.query_var = tk.StringVar()
        self.limit_var = tk.IntVar(value=20)
        self.status_var = tk.StringVar(value="Ready")

        self._build_widgets()
        self._poll_queue()

    def _build_widgets(self) -> None:
        """画面部品を組み立てます。"""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ツールバーフレーム
        toolbar_frame = ttk.Frame(self)
        toolbar_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        toolbar_frame.columnconfigure(0, weight=1)
        
        ttk.Button(toolbar_frame, text="Index Settings", command=self.open_settings).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar_frame, text="Show Stats", command=self.show_stats).pack(side="left")

        search_frame = ttk.LabelFrame(self, text="Search")
        search_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        search_frame.columnconfigure(0, weight=1)
        search_frame.rowconfigure(1, weight=1)

        search_controls = ttk.Frame(search_frame)
        search_controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        search_controls.columnconfigure(0, weight=1)

        query_entry = ttk.Entry(search_controls, textvariable=self.query_var)
        query_entry.grid(row=0, column=0, sticky="ew")
        query_entry.bind("<Return>", lambda _event: self.run_search())

        ttk.Label(search_controls, text="Limit").grid(row=0, column=1, padx=(8, 4))
        ttk.Spinbox(search_controls, from_=1, to=200, width=6, textvariable=self.limit_var).grid(row=0, column=2)
        ttk.Button(search_controls, text="Search", command=self.run_search).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(search_controls, text="Stats", command=self.show_stats).grid(row=0, column=4, padx=(8, 0))

        self.output = ScrolledText(search_frame, wrap="word", height=18)
        self.output.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

    def open_settings(self) -> None:
        """設定ウィンドウを開きます。既に開いている場合は前面に表示します。"""
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus()
        else:
            self.settings_window = SettingsWindow(
                self, 
                self.folder_var, 
                self.db_var, 
                self.workers_var, 
                self.batch_size_var,
                self.max_text_bytes_var,
                self.start_index,
                self.cancel_index,
            )

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

        workers = max(1, self.workers_var.get())
        batch_size = max(1, self.batch_size_var.get())
        max_text_bytes = max(0, self.max_text_bytes_var.get())
        self.cancel_event = threading.Event()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.set_cancel_button_state("normal")

        # 設定ウィンドウのボタンを無効化
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.set_index_button_state("disabled")
        
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.set_progress(100, 0, "0 / 0, remaining 0")
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
            if self.settings_window and self.settings_window.winfo_exists():
                self.settings_window.set_cancel_button_state("disabled")
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
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_progress(
                        maximum, current, f"{current} / {total}, remaining {remaining}"
                    )
                self.status_var.set(f"Indexing... {current}/{total} {label}")
            elif kind == "done":
                stats = cast(IndexStats, payload)
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_progress(
                        int(self.settings_window.progress["maximum"]),
                        int(self.settings_window.progress["maximum"]),
                        f"{stats.scanned} / {stats.scanned}, remaining 0",
                    )
                    self.settings_window.set_cancel_button_state("disabled")
                # 設定ウィンドウのボタンを有効化
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_index_button_state("normal")
                self.status_var.set("Index complete")
                self._append_output(
                    "Index complete: "
                    f"scanned {stats.scanned}, stored {stats.stored}, "
                    f"skipped {stats.skipped}, failed {stats.failed}\n"
                )
            elif kind == "cancelled":
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_cancel_button_state("disabled")
                    self.settings_window.set_index_button_state("normal")
                self.status_var.set("Index cancelled")
                stats = cast(IndexStats, payload)
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_progress(
                        int(self.settings_window.progress["maximum"]),
                        stats.scanned,
                        f"{stats.scanned} / {self.settings_window.progress['maximum']}, remaining 0",
                    )
                self._append_output(
                    "Index cancelled: "
                    f"scanned {stats.scanned}, stored {stats.stored}, "
                    f"skipped {stats.skipped}, failed {stats.failed}\n"
                )
            elif kind == "error":
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_progress(100, 0, "0 / 0, remaining 0")
                    self.settings_window.set_cancel_button_state("disabled")
                # 設定ウィンドウのボタンを有効化
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_index_button_state("normal")
                self.status_var.set("Error")
                messagebox.showerror("QFP", str(payload))

        self.after(100, self._poll_queue)

    def run_search(self) -> None:
        """検索を実行して結果を表示します。"""
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo("QFP", "Enter a search query.")
            return

        try:
            rows = search_files(Path(self.db_var.get()), query, self.limit_var.get())
        except Exception as exc:
            messagebox.showerror("QFP", str(exc))
            return

        self.output.delete("1.0", "end")
        if not rows:
            self._append_output("No files found.\n")
            return

        for index, row in enumerate(rows, start=1):
            self._append_output(f"{index}. {row['relative_path']}\n")
            self._append_output(f"   path: {row['path']}\n")
            self._append_output(f"   size: {row['size']} bytes\n")
            self._append_output(f"   modified: {row['modified_at']}\n")
            if row["snippet"]:
                snippet = row["snippet"].replace("\n", " ")
                self._append_output(f"   text: {snippet}\n")
            self._append_output("\n")

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
