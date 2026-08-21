"""JSON 設定ファイルを読み込み、アプリ全体の設定値を用意します。"""

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_DB = "index.sqlite3"
DEFAULT_WORKERS = min(4, (os.cpu_count() or 1) + 1)
DEFAULT_BATCH_SIZE = 1

CONFIG_DIR = Path(__file__).resolve().parent
DENY_PATTERNS_JSON = CONFIG_DIR / "deny_patterns.json"

# 設定ファイルがない場合でも最低限除外しておくフォルダ名です。
#DEFAULT_DENIED_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}
DEFAULT_DENIED_NAMES = {}
DEFAULT_DENIED_EXTENSIONS: set[str] = set()


def load_json(path: Path, default: Any) -> Any:
    """JSONファイルを読み込みます。存在しない場合は既定値で作成します。"""
    if not path.exists():
        write_json(path, default)
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    """親フォルダを作成してからJSONファイルを書き込みます。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def normalize_extension(value: str) -> str:
    """拡張子を .txt 形式の小文字にそろえます。"""
    value = value.strip().lower()
    if not value:
        return value
    return value if value.startswith(".") else f".{value}"


def normalize_name(value: str) -> str:
    """除外名を比較しやすい小文字にそろえます。"""
    return value.strip().lower()


def read_deny_patterns(path: Path) -> tuple[set[str], set[str]]:
    """deny_patterns.jsonから探索しない拡張子と名前を読み込みます。

    extensions は .exe のような拡張子、names は node_modules のような
    ファイル名またはフォルダ名を指定します。
    """
    default_data = {
        "extensions": sorted(DEFAULT_DENIED_EXTENSIONS),
        "names": sorted(DEFAULT_DENIED_NAMES),
    }
    data = load_json(path, default_data)
    if isinstance(data, list):
        names = {normalize_name(str(value)) for value in data if str(value).strip()}
        return set(DEFAULT_DENIED_EXTENSIONS), names

    extensions = data.get("extensions", []) if isinstance(data, dict) else []
    names = data.get("names", []) if isinstance(data, dict) else []
    return (
        {normalize_extension(str(value)) for value in extensions if str(value).strip()},
        {normalize_name(str(value)) for value in names if str(value).strip()},
    )


DENIED_EXTENSIONS, DENIED_NAMES = read_deny_patterns(DENY_PATTERNS_JSON)
