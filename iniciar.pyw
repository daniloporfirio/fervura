"""Entry point for double-click and Windows autostart (pythonw, no console)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fervura.app import main  # noqa: E402

main()
