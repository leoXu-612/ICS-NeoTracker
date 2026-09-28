"""Frozen desktop entry point; dispatch spawn helpers before importing Qt."""

from multiprocessing import freeze_support
import sys


if __name__ == "__main__":
    freeze_support()
    if sys.argv[1:3] == ["-m", "neo_tracker.ui.project_open_worker"]:
        from neo_tracker.ui.project_open_worker import _run_isolated_load_cli

        raise SystemExit(_run_isolated_load_cli(sys.argv[3:]))
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from bundle_smoke import run

        raise SystemExit(run(sys.argv[2]))
    from neo_tracker.__main__ import main

    raise SystemExit(main())
