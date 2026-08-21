"""フォルダを探索して DB に登録する機能を担当します。"""

import mimetypes
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

from file_indexer.config import DEFAULT_BATCH_SIZE, DEFAULT_WORKERS
from file_indexer.db import connect, init_db, upsert_file_records
from file_indexer.filesystem import iter_files, read_and_hash, should_read_text


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
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event: Event | None = None,
) -> IndexStats:
    """指定フォルダを探索して DB に登録します。

    workers は本文読み込みとハッシュ計算に使うワーカー数です。
    SQLite への書き込みは直列にします。
    workers=1 を指定すると並列化せずに処理します。
    """
    root = folder.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"フォルダが見つかりません: {folder}")

    logger = logger or (lambda message: print(message, file=sys.stderr))
    batch_size = max(1, batch_size)
    excluded_paths = build_excluded_paths(db_path)

    if log_enabled:
        logger("ファイル一覧を作成しています...")
    paths = list(iter_files(root, excluded_paths))

    stats = IndexStats()
    conn = connect(db_path)
    try:
        init_db(conn)
        paths, skipped_count = _filter_unchanged_paths(conn, paths)
        stats.skipped = skipped_count
        stats.scanned = skipped_count
        total = len(paths) + skipped_count
        if workers <= 1:
            _index_sequential(
                conn, root, paths, max_text_bytes, stats, total, progress_callback, log_enabled, logger,
                cancel_event, batch_size,
            )
        else:
            _index_parallel(
                conn, root, paths, max_text_bytes, stats, total, progress_callback, log_enabled, logger, workers,
                cancel_event, batch_size,
            )
        conn.commit()
    finally:
        conn.close()

    return stats


def build_excluded_paths(db_path: Path) -> set[Path]:
    """探索対象から外す DB 本体と WAL/SHM ファイルのパスを返します。"""
    resolved_db_path = db_path.resolve()
    return {
        resolved_db_path,
        Path(str(resolved_db_path) + "-wal"),
        Path(str(resolved_db_path) + "-shm"),
    }


def _filter_unchanged_paths(conn, paths: list[Path]) -> tuple[list[Path], int]:
    """DB に既に同じサイズと更新時刻があればハッシュ計算をスキップします。"""
    filtered: list[Path] = []
    skipped = 0
    existing_files = {
        row["path"]: (row["size"], row["modified_at"])
        for row in conn.execute("SELECT path, size, modified_at FROM files")
    }

    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue

        absolute_path = path.resolve()
        existing = existing_files.get(str(absolute_path))
        if existing is None:
            filtered.append(path)
            continue

        current_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        if existing == (stat.st_size, current_modified):
            skipped += 1
            continue

        filtered.append(path)

    return filtered, skipped


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
    batch_size: int,
) -> None:
    """ワーカー数 1 のときの直列インデックス処理です。"""
    pending: list[PreparedFile] = []
    for path in paths:
        if cancel_event and cancel_event.is_set():
            break
        try:
            prepared = prepare_file(root, path, max_text_bytes)
            stats.scanned += 1
            if prepared is None:
                stats.skipped += 1
            else:
                pending.append(prepared)
                stats.stored += 1
        except OSError as exc:
            stats.scanned += 1
            stats.failed += 1
            if log_enabled:
                logger(f"読み取り失敗: {path} ({exc})")
        finally:
            if len(pending) >= batch_size:
                _flush_prepared_files(conn, pending)
                pending.clear()
            notify_progress(progress_callback, stats.scanned, total, path.name)

    _flush_prepared_files(conn, pending)


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
    batch_size: int,
) -> None:
    """ファイルの読み込み・ハッシュ計算を並列化して、DB 保存は直列に行います。"""
    pending: list[PreparedFile] = []
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
                    pending.append(prepared)
                    stats.stored += 1
            except OSError as exc:
                stats.scanned += 1
                stats.failed += 1
                if log_enabled:
                    logger(f"読み取り失敗: {path} ({exc})")
            finally:
                if len(pending) >= batch_size:
                    _flush_prepared_files(conn, pending)
                    pending.clear()
                notify_progress(progress_callback, stats.scanned, total, path.name)
    finally:
        if cancel_event and cancel_event.is_set():
            for future in futures:
                future.cancel()
        executor.shutdown(wait=True, cancel_futures=bool(cancel_event and cancel_event.is_set()))
    _flush_prepared_files(conn, pending)


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
    sha256, content = read_and_hash(path, should_read_text(path, max_text_bytes))

    return PreparedFile(
        root=root,
        absolute_path=absolute_path,
        relative_path=relative_path,
        name=path.name,
        extension=path.suffix.lower(),
        size=stat.st_size,
        modified_at=modified_at,
        mime_type=mime_type,
        sha256=sha256,
        content=content,
        indexed_at=indexed_at,
    )


def _flush_prepared_files(conn, prepared_files: list[PreparedFile]) -> None:
    """準備済みファイルをまとめてDBへ保存します。"""
    records = [
        {
            "root": str(prepared.root),
            "path": str(prepared.absolute_path),
            "relative_path": prepared.relative_path,
            "name": prepared.name,
            "extension": prepared.extension,
            "size": prepared.size,
            "modified_at": prepared.modified_at,
            "mime_type": prepared.mime_type,
            "sha256": prepared.sha256,
            "content": prepared.content,
            "indexed_at": prepared.indexed_at,
        }
        for prepared in prepared_files
    ]
    upsert_file_records(conn, records)
