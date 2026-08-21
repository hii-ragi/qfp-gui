"""DB に登録済みのファイルを検索・集計する機能を担当します。"""

import sqlite3
from pathlib import Path

from file_indexer.db import connect, init_db


def search_files(db_path: Path, query: str, limit: int) -> list[sqlite3.Row]:
    """FTS5 を優先し、使えない検索語や環境では LIKE 検索に切り替えます。"""
    conn = connect(db_path)
    try:
        fts_enabled = init_db(conn)
        if fts_enabled:
            try:
                rows = search_files_fts(conn, query, limit)
                if rows:
                    return rows
            except sqlite3.OperationalError:
                pass

        return search_files_like(conn, query, limit)
    finally:
        conn.close()


def search_files_fts(conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    """FTS5 を使った全文検索です。"""
    return conn.execute(
        """
        SELECT files.path, files.relative_path, files.size, files.modified_at,
               snippet(files_fts, 3, '[', ']', ' ... ', 12) AS snippet
        FROM files_fts
        JOIN files ON files.id = files_fts.file_id
        WHERE files_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()


def search_files_like(conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    """FTS5 が使えない場合の素朴な部分一致検索です。"""
    like_query = f"%{query}%"
    return conn.execute(
        """
        SELECT path, relative_path, size, modified_at,
               substr(content, 1, 160) AS snippet
        FROM files
        WHERE name LIKE ?
           OR relative_path LIKE ?
           OR content LIKE ?
        ORDER BY relative_path
        LIMIT ?
        """,
        (like_query, like_query, like_query, limit),
    ).fetchall()


def show_stats(db_path: Path) -> sqlite3.Row:
    """登録済みファイル数などの概要を返します。"""
    conn = connect(db_path)
    try:
        init_db(conn)
        return conn.execute(
            """
            SELECT COUNT(*) AS file_count,
                   COALESCE(SUM(size), 0) AS total_bytes,
                   COALESCE(MAX(indexed_at), '') AS last_indexed_at
            FROM files
            """
        ).fetchone()
    finally:
        conn.close()
