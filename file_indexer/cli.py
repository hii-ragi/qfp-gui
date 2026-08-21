"""コマンドライン引数の処理と画面出力を担当します。"""

import argparse
import sqlite3
import sys
from pathlib import Path

from file_indexer.config import DEFAULT_BATCH_SIZE, DEFAULT_DB, DEFAULT_WORKERS
from file_indexer.indexing import index_folder
from file_indexer.search import search_files, show_stats


def build_parser() -> argparse.ArgumentParser:
    """CLI の引数定義を作ります。"""
    parser = argparse.ArgumentParser(
        description="指定フォルダのファイル情報とテキスト内容を SQLite に記録して検索します。"
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite DB のパス。既定: {DEFAULT_DB}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="フォルダ内を探索して DB に記録します。")
    index_parser.add_argument("folder", help="探索するフォルダ")
    index_parser.add_argument(
        "--max-text-bytes",
        type=int,
        default=1024 * 1024,
        help="本文検索用に読み込む最大ファイルサイズ。既定: 1048576",
    )
    index_parser.add_argument(
        "--workers",
        "--hash-workers",
        dest="workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"本文読み込み・ハッシュ計算に使う並列ワーカー数。1 で並列化なし。既定: {DEFAULT_WORKERS}",
    )
    index_parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"DBへ一括保存するファイル数。既定: {DEFAULT_BATCH_SIZE}",
    )
    index_parser.add_argument("--quiet", action="store_true", help="ログメッセージを表示しません。")

    search_parser = subparsers.add_parser("search", help="DB に記録したファイルを検索します。")
    search_parser.add_argument("query", help="検索語。例: report OR main.py")
    search_parser.add_argument("--limit", type=int, default=20, help="表示件数。既定: 20")

    subparsers.add_parser("stats", help="DB の登録件数を表示します。")
    return parser


def main() -> int:
    """CLI のエントリーポイントです。"""
    configure_console_output()
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db).resolve()

    try:
        if args.command == "index":
            return run_index(args, db_path)
        if args.command == "search":
            return run_search(args, db_path)
        if args.command == "stats":
            return run_stats(db_path)
    except (sqlite3.Error, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


def configure_console_output() -> None:
    """Windows コンソールで表現できない文字があっても出力を止めないようにします。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def run_index(args: argparse.Namespace, db_path: Path) -> int:
    """index サブコマンドを実行します。"""
    stats = index_folder(
        db_path,
        Path(args.folder),
        args.max_text_bytes,
        show_progress=False,
        log_enabled=not args.quiet,
        workers=args.workers,
        batch_size=args.batch_size,
    )
    if not args.quiet:
        print(f"DB: {db_path}")
        print(
            "探索完了: "
            f"確認 {stats.scanned} 件 / 登録 {stats.stored} 件 / "
            f"スキップ {stats.skipped} 件 / 失敗 {stats.failed} 件"
        )
    return 0


def run_search(args: argparse.Namespace, db_path: Path) -> int:
    """search サブコマンドを実行します。"""
    rows = search_files(db_path, args.query, args.limit)
    if not rows:
        print("該当するファイルはありませんでした。")
        return 0

    for index, row in enumerate(rows, start=1):
        print(f"{index}. {row['relative_path']}")
        print(f"   path: {row['path']}")
        print(f"   size: {row['size']} bytes")
        print(f"   modified: {row['modified_at']}")
        if row["snippet"]:
            print(f"   text: {row['snippet'].replace(chr(10), ' ')}")
    return 0


def run_stats(db_path: Path) -> int:
    """stats サブコマンドを実行します。"""
    stats = show_stats(db_path)
    print(f"DB: {db_path}")
    print(f"files: {stats['file_count']}")
    print(f"bytes: {stats['total_bytes']}")
    print(f"last_indexed_at: {stats['last_indexed_at']}")
    return 0
