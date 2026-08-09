# Application Shell Delivery Manifest

## Branch and base

- Branch: `refactor/gpt-application-shell`
- Fixed base: `9ccc14baedaf19ad5160efdc325fb151d49c3199`
- Implementation/audit head before this manifest: `bb493e6`
- Main merge: not performed, as required by the work package.

## Commit sequence

```text
dbd7016 chore(architecture): freeze application shell baseline
c6470c8 refactor(app): introduce task supervision and extract job state
a92b827 refactor(app): extract preview and playback coordination
95666cd refactor(app): extract media import coordination
5d106d9 refactor(app): extract project io coordination
c921eee refactor(app): extract tracking lifecycle coordinator
99681ee refactor(app): extract analysis and review-response coordination
a7491be refactor(ui): reduce NeoTrackerWindow to an application shell
bb493e6 test(architecture): make Qt coordinator suite order-independent
```

The final documentation-only commit adds this manifest; its SHA is reported by
the delivering agent after commit creation.

## Complete changed-file inventory

Status is relative to the fixed base.  This list includes this manifest.

```text
A artifacts/parallel-gpt-2026-08-09/baseline/architecture-inventory.md
A artifacts/parallel-gpt-2026-08-09/baseline/baseline-summary.md
A artifacts/parallel-gpt-2026-08-09/baseline/compileall.txt
A artifacts/parallel-gpt-2026-08-09/baseline/full-unittest.txt
A artifacts/parallel-gpt-2026-08-09/baseline/pip-check.txt
A artifacts/parallel-gpt-2026-08-09/baseline/project-open-100k.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/baseline/project-open-100k.json
A artifacts/parallel-gpt-2026-08-09/baseline/project-open-100k.stderr.txt
A artifacts/parallel-gpt-2026-08-09/baseline/targeted-lifecycle.txt
A artifacts/parallel-gpt-2026-08-09/final-audit/README.md
A artifacts/parallel-gpt-2026-08-09/final-audit/delivery-manifest.md
A artifacts/parallel-gpt-2026-08-09/g3/compileall.txt
A artifacts/parallel-gpt-2026-08-09/g3/g3-summary.md
A artifacts/parallel-gpt-2026-08-09/g3/pip-check.txt
A artifacts/parallel-gpt-2026-08-09/g3/project-open-100k.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g3/project-open-100k.json
A artifacts/parallel-gpt-2026-08-09/g3/project-open-100k.stderr.txt
A artifacts/parallel-gpt-2026-08-09/g4/compileall.txt
A artifacts/parallel-gpt-2026-08-09/g4/full-unittest.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g4/full-unittest.txt
A artifacts/parallel-gpt-2026-08-09/g4/g4-summary.md
A artifacts/parallel-gpt-2026-08-09/g4/pip-check.txt
A artifacts/parallel-gpt-2026-08-09/g4/project-open-100k-confirmation.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g4/project-open-100k-confirmation.json
A artifacts/parallel-gpt-2026-08-09/g4/project-open-100k-confirmation.stderr.txt
A artifacts/parallel-gpt-2026-08-09/g4/project-open-100k.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g4/project-open-100k.json
A artifacts/parallel-gpt-2026-08-09/g4/project-open-100k.stderr.txt
A artifacts/parallel-gpt-2026-08-09/g4/tracking-state-matrix.md
A artifacts/parallel-gpt-2026-08-09/g5/full-unittest.txt
A artifacts/parallel-gpt-2026-08-09/g5/g5-summary.md
A artifacts/parallel-gpt-2026-08-09/g6/compileall.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g6/compileall.txt
A artifacts/parallel-gpt-2026-08-09/g6/final-architecture-inventory.md
A artifacts/parallel-gpt-2026-08-09/g6/full-unittest.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g6/full-unittest.txt
A artifacts/parallel-gpt-2026-08-09/g6/g6-summary.md
A artifacts/parallel-gpt-2026-08-09/g6/pip-check.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g6/pip-check.txt
A artifacts/parallel-gpt-2026-08-09/g6/project-open-100k-confirmation.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g6/project-open-100k-confirmation.json
A artifacts/parallel-gpt-2026-08-09/g6/project-open-100k-confirmation.stderr.txt
A artifacts/parallel-gpt-2026-08-09/g6/project-open-100k.exit-code.txt
A artifacts/parallel-gpt-2026-08-09/g6/project-open-100k.json
A artifacts/parallel-gpt-2026-08-09/g6/project-open-100k.stderr.txt
M collab/FROM_CODEX.md
A neo_tracker/application/__init__.py
A neo_tracker/application/analysis_coordinator.py
A neo_tracker/application/job_state.py
A neo_tracker/application/media_import_coordinator.py
A neo_tracker/application/playback_coordinator.py
A neo_tracker/application/preview_coordinator.py
A neo_tracker/application/project_io_coordinator.py
A neo_tracker/application/review_response_coordinator.py
A neo_tracker/application/task_supervisor.py
A neo_tracker/application/tracking_coordinator.py
A neo_tracker/ui/action_registry.py
M neo_tracker/ui/background_tasks.py
M neo_tracker/ui/main_window.py
A neo_tracker/ui/shell/__init__.py
A neo_tracker/ui/shell/bindings.py
A neo_tracker/ui/shell/main_shell.py
A neo_tracker/ui/view_state.py
A tests/test_action_registry.py
A tests/test_analysis_coordinator.py
A tests/test_application_shell.py
A tests/test_application_task_supervisor.py
A tests/test_main_window_architecture.py
A tests/test_media_import_coordinator.py
A tests/test_playback_coordinator.py
A tests/test_preview_coordinator.py
A tests/test_project_io_coordinator.py
A tests/test_review_response_coordinator.py
A tests/test_tracking_coordinator.py
M tests/test_ui_main_window.py
A tests/test_view_state.py
```

## Validation references

- Final current validation and benchmark: `final-audit/README.md`
- Structural comparison and responsibility map:
  `g6/final-architecture-inventory.md`
- Per-stage handoff: `collab/FROM_CODEX.md`
