"""フォルダ内のファイルを SQLite に記録して検索するためのパッケージです。"""

from file_indexer.config import DEFAULT_DB
from file_indexer.indexing import IndexStats, index_folder
from file_indexer.search import search_files, show_stats

__all__ = ["DEFAULT_DB", "IndexStats", "index_folder", "search_files", "show_stats"]
