"""Entry point for the PyInstaller build (OMNI.exe)."""

import multiprocessing

from omni.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
