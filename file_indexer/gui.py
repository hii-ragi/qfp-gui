"""Tkinter を使った簡易 GUI を提供します。"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from file_indexer.config import DEFAULT_DB, DEFAULT_WORKERS
from file_indexer.indexing import index_folder
from file_indexer.search import search_files, show_stats


class SettingsWindow(tk.Toplevel):
    """設定ウィンドウを表示するクラスです。"""

    def __init__(self, parent: tk.Tk, folder_var: tk.StringVar, db_var: tk.StringVar, 
                 workers_var: tk.IntVar, max_text_bytes_var: tk.IntVar,
                 on_index_start: callable) -> None:
        super().__init__(parent)
        self.title("Index Settings")
        self.geometry("600x300")
        self.resizable(True, True)
        
        self.folder_var = folder_var
        self.db_var = db_var
        self.workers_var = workers_var
        self.max_text_bytes_var = max_text_bytes_var
        self.on_index_start = on_index_start
        self.parent = parent
        self.index_button: ttk.Button | None = None
        
        self._build_widgets()
        
        # ウィンドウが閉じられる時の処理
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _build_widgets(self) -> None:
        """画面部品を組み立てます。"""
        self.columnconfigure(0, weight=1)
        
        # Folder
        FolderEntry = ttk.Frame(self)
        FolderEntry.grid(row=0, column=0, columnspan=5, sticky="ew", padx=12, pady=(12, 6))
        FolderEntry.columnconfigure(1, weight=1)
        ttk.Label(FolderEntry, text="Folder", width=8).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        ttk.Entry(FolderEntry, textvariable=self.folder_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=12, pady=(12, 6))
        ttk.Button(FolderEntry, text="Browse", command=self.choose_folder).grid(row=0, column=4, padx=(0, 12), pady=(12, 6))

        # Database
        DatabaseEntry = ttk.Frame(self)
        DatabaseEntry.grid(row=1, column=0, columnspan=5, sticky="ew", padx=12, pady=(12, 6))
        DatabaseEntry.columnconfigure(1, weight=1)
        ttk.Label(DatabaseEntry, text="Database", width=8).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        ttk.Entry(DatabaseEntry, textvariable=self.db_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=12, pady=(12, 6))
        ttk.Button(DatabaseEntry, text="Browse", command=self.choose_db).grid(row=0, column=4, padx=(0, 12), pady=(12, 6))

        # Workers and Max text bytes
        options_frame = ttk.Frame(self)
        options_frame.grid(row=2, column=0, columnspan=5, sticky="ew", padx=12, pady=12)
        options_frame.columnconfigure(1, weight=0)
        options_frame.columnconfigure(3, weight=1)
        
        ttk.Label(options_frame, text="Workers", width=8).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options_frame, from_=1, to=32, width=6, textvariable=self.workers_var).grid(row=0, column=1, padx=(6, 18))
        
        ttk.Label(options_frame, text="Max text bytes", width=12).grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options_frame, from_=1024, to=100 * 1024 * 1024, increment=1024, width=12, textvariable=self.max_text_bytes_var).grid(
            row=0, column=3, padx=(6, 0), sticky="w"
        )

        # Button frame
        button_frame = ttk.Frame(self)
        button_frame.grid(row=3, column=0, columnspan=5, sticky="ew", padx=12, pady=(0, 12))
        button_frame.columnconfigure(0, weight=1)
        
        ttk.Button(button_frame, text="Start Index", command=self.start_index).grid(row=4, column=0, sticky="w")
        ttk.Button(button_frame, text="Close", command=self.on_closing).grid(row=5, column=1, sticky="w")

    def choose_folder(self) -> None:
        """インデックス対象フォルダを選択します。"""
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.cwd()))
        if folder:
            self.folder_var.set(folder)

    def choose_db(self) -> None:
        """SQLite DB ファイルを選択します。存在しないパスも指定できます。"""
        db_path = filedialog.asksaveasfilename(
            initialfile=Path(self.db_var.get()).name or DEFAULT_DB,
            defaultextension=".sqlite3",
            filetypes=[("SQLite database", "*.sqlite3 *.db"), ("All files", "*.*")],
        )
        if db_path:
            self.db_var.set(db_path)

    def start_index(self) -> None:
        """インデックス作成を開始します。"""
        self.on_index_start()

    def set_index_button_state(self, state: str) -> None:
        """インデックスボタンの状態を設定します。"""
        if self.index_button:
            self.index_button.configure(state=state)

    def on_closing(self) -> None:
        """ウィンドウを閉じます。"""
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
        self.settings_window: SettingsWindow | None = None

        self.folder_var = tk.StringVar(value=str(Path.cwd()))
        self.db_var = tk.StringVar(value=str((Path.cwd() / DEFAULT_DB).resolve()))
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        self.max_text_bytes_var = tk.IntVar(value=1024 * 1024)
        self.query_var = tk.StringVar()
        self.limit_var = tk.IntVar(value=20)
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="0 / 0, remaining 0")

        self._build_widgets()
        self._poll_queue()

    def _build_widgets(self) -> None:
        """画面部品を組み立てます。"""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ツールバーフレーム
        toolbar_frame = ttk.Frame(self)
        toolbar_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        toolbar_frame.columnconfigure(0, weight=1)
        
        ttk.Button(toolbar_frame, text="Index Settings", command=self.open_settings).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar_frame, text="Show Stats", command=self.show_stats).pack(side="left")

        progress_frame = ttk.Frame(self)
        progress_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        progress_frame.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.progress_text_var, width=28, anchor="e").grid(
            row=0, column=1, padx=(10, 0)
        )

        search_frame = ttk.LabelFrame(self, text="Search")
        search_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
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
        status.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))

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
                self.max_text_bytes_var,
                self.start_index
            )

    def start_index(self) -> None:
        """バックグラウンドでインデックス作成を開始します。"""
        if self.index_thread and self.index_thread.is_alive():
            messagebox.showinfo("QFP", "Indexing is already running.")
            return

        folder = Path(self.folder_var.get())
        db_path = Path(self.db_var.get())
        workers = max(1, self.workers_var.get())
        max_text_bytes = max(0, self.max_text_bytes_var.get())

        # 設定ウィンドウのボタンを無効化
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.set_index_button_state("disabled")
        
        self.progress.configure(maximum=100, value=0)
        self.progress_text_var.set("0 / 0, remaining 0")
        self.status_var.set("Indexing...")
        self._append_output("Index started.\n")

        self.index_thread = threading.Thread(
            target=self._index_worker,
            args=(db_path, folder, max_text_bytes, workers),
            daemon=True,
        )
        self.index_thread.start()

    def _index_worker(self, db_path: Path, folder: Path, max_text_bytes: int, workers: int) -> None:
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
                progress_callback=lambda current, total, label: self.message_queue.put(
                    ("progress", (current, total, label))
                ),
            )
            self.message_queue.put(("done", stats))
        except Exception as exc:
            self.message_queue.put(("error", exc))

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
                current, total, label = payload
                maximum = max(total, 1)
                remaining = max(total - current, 0)
                self.progress.configure(maximum=maximum)
                self.progress.configure(value=current)
                self.progress_text_var.set(f"{current} / {total}, remaining {remaining}")
                self.status_var.set(f"Indexing... {current}/{total} {label}")
            elif kind == "done":
                self.progress.configure(value=self.progress["maximum"])
                # 設定ウィンドウのボタンを有効化
                if self.settings_window and self.settings_window.winfo_exists():
                    self.settings_window.set_index_button_state("normal")
                self.status_var.set("Index complete")
                stats = payload
                self.progress_text_var.set(f"{stats.scanned} / {stats.scanned}, remaining 0")
                self._append_output(
                    "Index complete: "
                    f"scanned {stats.scanned}, stored {stats.stored}, "
                    f"skipped {stats.skipped}, failed {stats.failed}\n"
                )
            elif kind == "error":
                self.progress.configure(value=0)
                self.progress_text_var.set("0 / 0, remaining 0")
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
