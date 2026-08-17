"""フォルダ探索、テキスト読み込み、ハッシュ計算などのファイル操作を担当します。"""

import hashlib
import os
from pathlib import Path

from file_indexer.config import DENIED_EXTENSIONS, DENIED_NAMES


def iter_files(root: Path, excluded_paths: set[Path]):
    """探索対象のファイルを返します。deny設定とDB自身は対象から外します。"""
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if not is_denied_name(name)]
        for file_name in file_names:
            path = Path(current_root) / file_name
            if should_skip_path(path, excluded_paths):
                continue
            yield path


def should_skip_path(path: Path, excluded_paths: set[Path]) -> bool:
    """deny_patterns.jsonやDB都合で探索から外すべきパスか判定します。"""
    if is_denied_name(path.name) or path.suffix.lower() in DENIED_EXTENSIONS:
        return True

    try:
        return path.resolve() in excluded_paths
    except OSError:
        return False


def is_denied_name(name: str) -> bool:
    """deny_patterns.jsonに書かれた名前かどうかを判定します。"""
    return name.lower() in DENIED_NAMES


def count_files(root: Path, excluded_paths: set[Path]) -> int:
    """必要な場合に、探索対象件数を数えます。"""
    return sum(1 for _ in iter_files(root, excluded_paths))


def should_read_text(path: Path, max_bytes: int) -> bool:
    """本文検索用に読み込むファイルか判定します。

    以前は許可拡張子リストにあるファイルだけを本文読み込み対象にしていました。
    現在は標準ですべてのファイルを対象にし、deny_patterns.jsonで除外された
    ファイルだけを探索段階で外します。ここではサイズ上限だけを確認します。
    """
    try:
        return path.stat().st_size <= max_bytes
    except OSError:
        return False


def read_text(path: Path) -> str:
    """UTF-8とWindows日本語環境でよく使うcp932を試して読み込みます。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def file_hash(path: Path) -> str:
    """重複や変更確認に使えるよう、ファイルのSHA-256を返します。"""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
