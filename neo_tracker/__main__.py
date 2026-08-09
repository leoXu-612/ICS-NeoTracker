from __future__ import annotations


def main() -> int:
    try:
        from neo_tracker.ui.main_window import run
    except ImportError as exc:
        print("Neo-Tracker desktop shell requires PySide6.")
        print(f"Import error: {exc}")
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

