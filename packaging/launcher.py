"""Punto de entrada de los ejecutables (PyInstaller no admite el ``__main__`` de un paquete)."""

import sys

from lolcoach.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
