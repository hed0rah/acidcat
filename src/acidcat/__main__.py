"""Allow running as `python -m acidcat`."""
import sys
from acidcat.cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
