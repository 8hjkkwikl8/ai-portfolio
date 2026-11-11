"""一键离线演示：python demo.py"""

import sys

from promptlens.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["demo", *sys.argv[1:]]))
