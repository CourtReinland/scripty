"""Frozen entry point for the Scripty backend (PyInstaller target).

Delegates straight to the Typer CLI so the frozen binary behaves exactly like
`scripty <command>` (serve, demo, create, pass, ...).
"""
import multiprocessing

from scripty.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
