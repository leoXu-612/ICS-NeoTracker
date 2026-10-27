from __future__ import annotations


def main() -> int:
    import multiprocessing
    import sys

    multiprocessing.freeze_support()
    if sys.argv[1:2] == ["--isolated-load"]:
        from neo_tracker.ui.project_open_worker import _run_isolated_load_cli

        return _run_isolated_load_cli(sys.argv[1:])
    if sys.argv[1:2] == ["--smoke-test"]:
        from neo_tracker.release_smoke import main as smoke_main

        return smoke_main(sys.argv[2:])
    try:
        from neo_tracker.ui.main_window import run
    except ImportError as exc:
        print("Neo-Tracker desktop shell requires PySide6.")
        print(f"Import error: {exc}")
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

