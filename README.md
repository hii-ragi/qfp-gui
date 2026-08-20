# qfp-gui

フォルダ内のファイルを SQLite にインデックス登録し、ファイル名・パス・本文から検索するデスクトップアプリケーションです。GUI とコマンドラインの両方に対応しています。

## 主な機能

- 指定したフォルダを再帰的に探索してファイルを登録
- ファイルのパス、相対パス、名前、拡張子、サイズ、更新日時、MIMEタイプ、SHA-256ハッシュを保存
- サイズ上限以下のファイル本文を読み込み、本文検索に対応
- SQLite FTS5 が利用できる環境では全文検索、利用できない場合は部分一致検索に自動切り替え
- 同じパスを再度インデックスすると既存レコードを更新
- インデックス作成中の進捗表示、ログ表示、バックグラウンド実行
- 登録ファイル数、合計サイズ、最終インデックス日時の表示
- ファイル読み込みとハッシュ計算の並列処理
- 特定のファイル名や拡張子の除外設定

## 必要環境

- Windows, linux
- Python 3.10 以降を想定
- 外部ライブラリは不要です。Tkinter、SQLite、FTS5 など Python 標準ライブラリを使用します。

## 起動

### GUI
#### exe実行する
releaseにビルド済みパッケージが存在します。(exeファイル)

#### CLIからパッケージをそろえて実行する
```powershell
python qfp-gui.py
```

引数なしで起動するとGUIが開きます。

1. `Index Settings` で対象フォルダとSQLite DBの保存先を指定します。
2. 必要に応じて `Workers` と `Max text bytes` を設定します。
3. `Start Index` を押してインデックスを作成します。
4. 検索欄に検索語を入力し、`Search` を押します。Enterキーでも検索できます。
5. `Stats` または上部の `Show Stats` でDBの統計を表示します。

### CLI

#### インデックス作成

```powershell
python qfp-gui.py index C:\path\to\folder
```

DBの保存先は既定でカレントフォルダの `index.sqlite3` です。保存先、本文読み込み上限、ワーカー数は変更できます。

```powershell
python qfp-gui.py --db C:\path\to\my-index.sqlite3 index C:\path\to\folder --max-text-bytes 2097152 --workers 4
```

ログを表示しない場合は `--quiet` を指定します。

#### 検索

```powershell
python qfp-gui.py search "report OR main.py"
python qfp-gui.py search "設計" --limit 50
```

検索結果には相対パス、絶対パス、ファイルサイズ、更新日時、本文スニペットが表示されます。検索語はまずSQLite FTS5で処理され、FTS5で検索できない場合や結果がない場合はファイル名・相対パス・本文の部分一致検索に切り替わります。

#### 統計表示

```powershell
python qfp-gui.py stats
python qfp-gui.py --db C:\path\to\my-index.sqlite3 stats
```

## インデックスの仕様

- フォルダは再帰的に探索します。
- DB本体と、そのSQLite WAL/SHMファイルは探索対象から除外します。
- `file_indexer/deny_patterns.json` に指定した拡張子、ファイル名、フォルダ名は除外します。
- 本文は既定で1MiB以下のファイルだけ読み込みます。上限を超えるファイルもメタデータとハッシュは登録しますが、本文検索の対象にはなりません。
- 本文の読み込みは `UTF-8 BOM付き`、`UTF-8`、`cp932` の順で試行し、いずれも判定できない場合は置換文字を使って読み込みます。
- インデックス作成では本文の読み込みとSHA-256計算を並列化できます。SQLiteへの保存は直列に行います。Index Settingの`workers` またはCLI実行では `--workers 1` で並列化を無効にできます。

## 除外設定

`file_indexer/deny_patterns.json` を編集すると、探索しない拡張子や名前を追加できます。

```json
{
	"extensions": [".exe", ".zip"],
	"names": [".git", "node_modules", "build"]
}
```

拡張子と名前の比較は大文字小文字を区別しません。拡張子は `.txt` と `txt` のどちらでも指定できます。

## 保存先DB

SQLite DBは指定したパスに自動作成され、親フォルダも必要に応じて作成されます。

## パッケージ化

PyInstaller用の `qfp-gui.spec` を同梱しています。GUIアプリとしてビルドする場合の例です。

```powershell
pyinstaller qfp-gui.spec
```

生成された実行ファイルは `dist` フォルダに出力されます。
