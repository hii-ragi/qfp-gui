"""フォルダを探索して DB に登録する機能を担当します。"""

import mimetypes
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

from file_indexer.config import DEFAULT_WORKERS
from file_indexer.db import connect, init_db, upsert_file_record
from file_indexer.filesystem import file_hash, iter_files, read_text, should_read_text


@dataclass
class IndexStats:
    scanned: int = 0
    stored: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class PreparedFile:
    root: Path
    absolute_path: Path
    relative_path: str
    name: str
    extension: str
    size: int
    modified_at: str
    mime_type: str | None
    sha256: str
    content: str
    indexed_at: str


def index_folder(
    db_path: Path,
    folder: Path,
    max_text_bytes: int,
    show_progress: bool = True,
    log_enabled: bool = True,
    logger: Callable[[str], None] | None = None,
    workers: int = DEFAULT_WORKERS,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event: Event | None = None,
) -> IndexStats:
    """指定フォルダを探索して DB に登録します。

    ファイル読み込みやハッシュ計算は並列化し、SQLite への書き込みは直列にします。
    workers=1 を指定すると並列化せずに処理します。
    """
    root = folder.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"フォルダが見つかりません: {folder}")

    logger = logger or (lambda message: print(message, file=sys.stderr))
    excluded_paths = build_excluded_paths(db_path)

    if log_enabled:
        logger("ファイル一覧を作成しています...")
    paths = list(iter_files(root, excluded_paths))
    total = len(paths)

    stats = IndexStats()
    with connect(db_path) as conn:
        init_db(conn)
        if workers <= 1:
            _index_sequential(
                conn, root, paths, max_text_bytes, stats, total, progress_callback, log_enabled, logger,
                cancel_event,
            )
        else:
            _index_parallel(
                conn, root, paths, max_text_bytes, stats, total, progress_callback, log_enabled, logger, workers,
                cancel_event,
            )
        conn.commit()

    return stats


def build_excluded_paths(db_path: Path) -> set[Path]:
    """探索対象から外す DB 本体と WAL/SHM ファイルのパスを返します。"""
    resolved_db_path = db_path.resolve()
    return {
        resolved_db_path,
        Path(str(resolved_db_path) + "-wal"),
        Path(str(resolved_db_path) + "-shm"),
    }


def _index_sequential(
    conn,
    root: Path,
    paths: list[Path],
    max_text_bytes: int,
    stats: IndexStats,
    total: int,
    progress_callback: Callable[[int, int, str], None] | None,
    log_enabled: bool,
    logger: Callable[[str], None],
    cancel_event: Event | None,
) -> None:
    """ワーカー数 1 のときの直列インデックス処理です。"""
    for path in paths:
        if cancel_event and cancel_event.is_set():
            break
        _store_prepared_path(
            conn, root, path, max_text_bytes, stats, total, progress_callback, log_enabled, logger
        )


def _index_parallel(
    conn,
    root: Path,
    paths: list[Path],
    max_text_bytes: int,
    stats: IndexStats,
    total: int,
    progress_callback: Callable[[int, int, str], None] | None,
    log_enabled: bool,
    logger: Callable[[str], None],
    workers: int,
    cancel_event: Event | None,
) -> None:
    """ファイルの読み込み・ハッシュ計算を並列化して、DB 保存は直列に行います。"""
    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {
        executor.submit(prepare_file, root, path, max_text_bytes): path
        for path in paths
    }
    try:
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break
            path = futures[future]
            try:
                prepared = future.result()
                stats.scanned += 1
                if prepared is None:
                    stats.skipped += 1
                else:
                    store_prepared_file(conn, prepared)
                    stats.stored += 1
            except OSError as exc:
                stats.scanned += 1
                stats.failed += 1
                if log_enabled:
                    logger(f"読み取り失敗: {path} ({exc})")
            finally:
                notify_progress(progress_callback, stats.scanned, total, path.name)
    finally:
        if cancel_event and cancel_event.is_set():
            for future in futures:
                future.cancel()
        executor.shutdown(wait=True, cancel_futures=bool(cancel_event and cancel_event.is_set()))


def _store_prepared_path(
    conn,
    root: Path,
    path: Path,
    max_text_bytes: int,
    stats: IndexStats,
    total: int,
    progress_callback: Callable[[int, int, str], None] | None,
    log_enabled: bool,
    logger: Callable[[str], None],
) -> None:
    """直列処理用に、1 パスを準備して保存します。"""
    try:
        prepared = prepare_file(root, path, max_text_bytes)
        stats.scanned += 1
        if prepared is None:
            stats.skipped += 1
        else:
            store_prepared_file(conn, prepared)
            stats.stored += 1
    except OSError as exc:
        stats.scanned += 1
        stats.failed += 1
        if log_enabled:
            logger(f"読み取り失敗: {path} ({exc})")
    finally:
        notify_progress(progress_callback, stats.scanned, total, path.name)


def notify_progress(
    progress_callback: Callable[[int, int, str], None] | None,
    current: int,
    total: int,
    label: str,
) -> None:
    if progress_callback:
        progress_callback(current, total, label)


def prepare_file(root: Path, path: Path, max_text_bytes: int) -> PreparedFile | None:
    """1 ファイル分の情報を作ります。DB には触らないため安全に並列実行できます。"""
    if not path.is_file():
        return None

    stat = path.stat()
    absolute_path = path.resolve()
    relative_path = absolute_path.relative_to(root).as_posix()
    modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    indexed_at = datetime.now(timezone.utc).isoformat()
    mime_type, _ = mimetypes.guess_type(str(path))
    content = read_text(path) if should_read_text(path, max_text_bytes) else ""

    return PreparedFile(
        root=root,
        absolute_path=absolute_path,
        relative_path=relative_path,
        name=path.name,
        extension=path.suffix.lower(),
        size=stat.st_size,
        modified_at=modified_at,
        mime_type=mime_type,
        sha256=file_hash(path),
        content=content,
        indexed_at=indexed_at,
    )


def store_prepared_file(conn, prepared: PreparedFile) -> None:
    """準備済みのファイル情報を DB 層へ渡します。"""
    upsert_file_record(
        conn=conn,
        root=prepared.root,
        absolute_path=prepared.absolute_path,
        relative_path=prepared.relative_path,
        name=prepared.name,
        extension=prepared.extension,
        size=prepared.size,
        modified_at=prepared.modified_at,
        mime_type=prepared.mime_type,
        sha256=prepared.sha256,
        content=prepared.content,
        indexed_at=prepared.indexed_at,
    )
