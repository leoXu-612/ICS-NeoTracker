"""Freeze support must run before importing the application or Qt."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from neo_tracker.__main__ import main

    raise SystemExit(main())
