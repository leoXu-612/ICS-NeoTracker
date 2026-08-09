# G7 Workspace Validation

## Conclusion

`WORKSPACE_READY`: **YES** for the GPT Analysis Workspace line at code head `ef55e43` on base `a0e9d94`. Data, Plot, Fit, SelectionSession, responsive layout, keyboard paths, accessibility text, and background fit coordination are implemented and pass the branch-local gates. Engine operation execution and project-generation wiring remain Integration responsibilities, not hidden UI computation.

## Test gates

| Gate | Result |
| --- | --- |
| Full suite after plot optimization | 594 tests, 0 fail/error, 66.022 s |
| Shell/Physics/Responsive/Architecture targeted | 27 tests, 0 fail/error |
| Plot + HiDPI/Responsive targeted | 11 tests, 0 fail/error |
| `compileall -q neo_tracker tests benchmarks` | exit 0 |
| `python3 -m pip check` | `No broken requirements found.` |
| `git diff --check` | clean after EOF whitespace repair |

The first post-G7 full run exposed three existing Shell gates: MainWindow exceeded 7000 lines, the new top-toolbar focus control raised minimum width to 1116 px, and a new Inspector tab changed the legacy tab set. They were fixed without weakening tests: physics integration moved into a Shell mixin, Canvas Focus moved to the Physics header under the same QAction, and the Inspector moved into the existing scrollable Review page. The exact three tests then passed 3/3 before the final full run.

## Responsive, keyboard, and accessibility evidence

- Offscreen window geometry tests pass at 1024×768, 1280×808, and 1440×900; 1024×768 remains exactly 1024×768.
- Canvas Focus hides the right sidebar and collapses the bottom workspace, remains reachable from the Physics header, and restores prior splitter/page/collapse state. Its transient collapse is not persisted.
- Bounded page/collapse/height state restores into a newly constructed window; numerical arrays never enter settings.
- Data controls pass forward Tab and reverse Shift+Tab traversal. Plot Left/Right commits one canonical selection revision per action.
- Table/Plot/Video selection tests cover exact/nearest matching and loop prevention.
- A 2× export produces the expected 800×400 physical image from a 400×200 logical plot. A pixel-semantic regression verifies that an invalid interval remains visually open after batched polyline drawing.
- Actions, empty/unavailable/error states, validity, provenance, units, cursor match, and collapse state all have readable text and accessible names/descriptions; color is not the only state channel.

These are Qt/offscreen tests, not a claim of native VoiceOver or system text-scaling validation.

## 100k evidence

The project-open gate ran at host load averages 3.33/3.22/3.34. All three background runs remained below the 75 ms heartbeat threshold: call 0.455–0.623 ms, apply 30.861–42.825 ms, heartbeat max 34.847–46.265 ms. Payload and fingerprint matched the synchronous reference.

The UI workload used 100,000 VFR-like samples at alternating 4/6 ms cadence, 99,725 valid samples, two explicit gaps, a 1280×360 plot, 31 table attaches, 11 plot prepares, and 21 paints:

| Measurement | P50 | P95 | Max |
| --- | ---: | ---: | ---: |
| Table attach | 0.0019 ms | 0.0028 ms | 0.0090 ms |
| Plot prepare | 3.103 ms | 4.002 ms | 4.799 ms |
| Plot paint | 27.763 ms | 28.047 ms | 28.113 ms |
| Paint-loop heartbeat | 26.851 ms | 29.179 ms | 31.954 ms |

The plot prepared 2,427 points (O(viewport width)), the table had 100,000 rows and zero cell widgets, peak process RSS was 96.33 MiB, no helper process was created, and the result digest was `9822bd5c2c582d43bfecdb7650fe6533535c5643cde92027c33df50eff5a9a6d`.

Dense envelopes use batched `QPolygonF` polylines and disable raster antialiasing only when prepared points exceed the logical viewport width; sparse plots remain antialiased. Endpoint/extrema/selection/range/gap invariants remain covered.

## Integration seams and limitations

- `NeoTrackerWindow.set_physics_series(...)` accepts immutable engine snapshots and rejects duplicate IDs, mixed revisions, and stale owners before UI attachment.
- `NeoTrackerWindow.set_kinematics_fit_operator(...)` injects only the `FitOperator` protocol. Fit work runs through the Application QThread coordinator; the UI performs no numerical fit.
- `NeoTrackerWindow.physicsOperationRequested` forwards immutable derivative, smoothing, and export requests. Integration must connect this signal to the real engine/export service and return new immutable series/results.
- Integration owns stable persisted task IDs/results generations and project schema. This branch uses bounded UI identities only and intentionally does not modify project/schema/controller/engine files.
- Non-OK fit terminals remain explicit text and cannot create a fit layer, selected fit ID, residual state, or export-enabled state.
