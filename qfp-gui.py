
import sys

from file_indexer.cli import main as cli_main
from file_indexer.gui import main as gui_main

if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli_main())
    raise SystemExit(gui_main())
