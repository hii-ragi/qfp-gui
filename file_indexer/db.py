"""SQLite への接続、初期化、保存処理を担当します。"""

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    """SQLite へ接続します。DB ファイルや親フォルダがなければ自動作成します。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def has_fts5(conn: sqlite3.Connection) -> bool:
    """SQLite に FTS5 が入っているかを実際に作成して確認します。"""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __fts5_check USING fts5(value)")
        conn.execute("DROP TABLE __fts5_check")
        return True
    except sqlite3.OperationalError:
        return False


def init_db(conn: sqlite3.Connection) -> bool:
    """ファイル情報テーブルと、利用できる場合は全文検索テーブルを準備します。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            root TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            extension TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            mime_type TEXT,
            sha256 TEXT,
            content TEXT,
            indexed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
        CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension);
        CREATE INDEX IF NOT EXISTS idx_files_relative_path ON files(relative_path);
        """
    )

    fts_enabled = has_fts5(conn)
    if fts_enabled:
        _ensure_fts_table(conn)
    conn.commit()
    return fts_enabled


def _ensure_fts_table(conn: sqlite3.Connection) -> None:
    """files.id と FTS の rowid を対応させ、旧形式は保存済み本文から移行します。"""
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'files_fts'"
    ).fetchone()
    if existing:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(files_fts)")}
        sql = "".join((existing["sql"] or "").lower().split())
        if "file_id" not in columns and "content=" not in sql:
            return

    # 作り直しと再登録を一体にし、失敗した場合は元のFTSを維持します。
    conn.execute("SAVEPOINT migrate_files_fts")
    try:
        if existing:
            conn.execute("DROP TABLE files_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE files_fts
            USING fts5(name, relative_path, content)
            """
        )
        conn.execute(
            """
            INSERT INTO files_fts(rowid, name, relative_path, content)
            SELECT id, name, relative_path, content FROM files
            """
        )
    except Exception:
        conn.execute("ROLLBACK TO migrate_files_fts")
        raise
    finally:
        conn.execute("RELEASE migrate_files_fts")


def upsert_file_record(
    conn: sqlite3.Connection,
    root: Path,
    absolute_path: Path,
    relative_path: str,
    name: str,
    extension: str,
    size: int,
    modified_at: str,
    mime_type: str | None,
    sha256: str,
    content: str,
    indexed_at: str,
) -> None:
    """1 ファイル分のメタデータと本文を files / files_fts に登録します。"""
    cursor = conn.execute(
        """
        INSERT INTO files (
            root, path, relative_path, name, extension, size, modified_at,
            mime_type, sha256, content, indexed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            root = excluded.root,
            relative_path = excluded.relative_path,
            name = excluded.name,
            extension = excluded.extension,
            size = excluded.size,
            modified_at = excluded.modified_at,
            mime_type = excluded.mime_type,
            sha256 = excluded.sha256,
            content = excluded.content,
            indexed_at = excluded.indexed_at
        RETURNING id
        """,
        (
            str(root),
            str(absolute_path),
            relative_path,
            name,
            extension,
            size,
            modified_at,
            mime_type,
            sha256,
            content,
            indexed_at,
        ),
    )
    row_id = cursor.fetchone()["id"]
    upsert_fts_record(conn, row_id, name, relative_path, content)


def upsert_file_records(conn: sqlite3.Connection, records: list[dict[str, object]]) -> None:
    """複数ファイルのメタデータとFTS情報をまとめて登録します。"""
    if not records:
        return

    conn.executemany(
        """
        INSERT INTO files (
            root, path, relative_path, name, extension, size, modified_at,
            mime_type, sha256, content, indexed_at
        )
        VALUES (:root, :path, :relative_path, :name, :extension, :size, :modified_at,
                :mime_type, :sha256, :content, :indexed_at)
        ON CONFLICT(path) DO UPDATE SET
            root = excluded.root,
            relative_path = excluded.relative_path,
            name = excluded.name,
            extension = excluded.extension,
            size = excluded.size,
            modified_at = excluded.modified_at,
            mime_type = excluded.mime_type,
            sha256 = excluded.sha256,
            content = excluded.content,
            indexed_at = excluded.indexed_at
        """,
        records,
    )

    paths = [record["path"] for record in records]
    placeholders = ", ".join("?" for _ in paths)
    rows = conn.execute(
        f"SELECT id, path FROM files WHERE path IN ({placeholders})", paths
    ).fetchall()
    ids_by_path = {row["path"]: row["id"] for row in rows}

    try:
        conn.executemany(
            "DELETE FROM files_fts WHERE rowid = ?",
            [(ids_by_path[record["path"]],) for record in records],
        )
        conn.executemany(
            "INSERT INTO files_fts(rowid, name, relative_path, content) VALUES (?, ?, ?, ?)",
            [
                (ids_by_path[record["path"]], record["name"], record["relative_path"], record["content"])
                for record in records
            ],
        )
    except sqlite3.OperationalError as exc:
        if str(exc) != "no such table: files_fts":
            raise


def upsert_fts_record(
    conn: sqlite3.Connection, row_id: int, name: str, relative_path: str, content: str
) -> None:
    """FTS5 の検索テーブルを更新します。FTS5 がない環境では何もしません。"""
    try:
        conn.execute("DELETE FROM files_fts WHERE rowid = ?", (row_id,))
        conn.execute(
            "INSERT INTO files_fts(rowid, name, relative_path, content) VALUES (?, ?, ?, ?)",
            (row_id, name, relative_path, content),
        )
    except sqlite3.OperationalError as exc:
        if str(exc) != "no such table: files_fts":
            raise
