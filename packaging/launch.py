"""Entry script for PyInstaller (the package's __main__ uses relative imports)."""

import sys

from cbapick4me.__main__ import main

if __name__ in ("__main__", "__mp_main__"):
    sys.exit(main())
