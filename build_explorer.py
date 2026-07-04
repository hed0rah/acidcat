#!/usr/bin/env python3
"""Back-compat shim. The explorer now lives in the acidcat package; prefer
`acidcat explore FILE -o out.html`. This still supports the documented pipe:
    acidcat inspect --full song.mp3 | python build_explorer.py -o song.html
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from acidcat.explorer import main  # noqa: E402

if __name__ == "__main__":
    main()
