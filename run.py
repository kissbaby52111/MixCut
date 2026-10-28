import sys
from pathlib import Path

packages = Path(__file__).resolve().parent / ".runtime/packages"
if packages.exists():
    sys.path.insert(0, str(packages))

from mixcut.cli import main

if __name__ == "__main__":
    main()
