# Neo-Tracker selected design A — implementation QA

Final result: passed — selected-direction native implementation, with the explicit adaptations below. This is not a pixel-identical image clone.

## Scope and evidence

The user selected the first generated image, now preserved at `artifacts/design-20261005/selected-a.png` (1488 × 1058, 1x). Implementation remains the existing PySide6 desktop app, not a website or a framework migration.

The comparison state is a loaded video with tracked points, a selected true-time sample, an actual fitted curve and three neighboring samples. The reference depicts an illustrative pendulum and invented millimeter values; the implementation deliberately uses a disposable 24-frame, 30 fps red-marker fixture with pixel coordinates and a genuine linear fit. The media and plot shape are therefore different by design, not copied artwork or scientific evidence. No Photos originals are used.

The reference includes drawn window buttons. App captures exclude the native macOS titlebar; no fake traffic lights are painted into the client area. System typography and native controls remain in use. Solid graphite replaces the reference's subtle material gradient to retain legibility and avoid a new rendering dependency.

## Comparison history

1. Source plus `source-smoke.png` opened together in one comparison input. Native capture 2976 × 2116 at DPR 2, logical viewport 1488 × 1058. Findings: P2 excessive bottom tabs and duplicate focus/export controls; P2 undersized quantity selector and truncated nearby timestamps; P2 dark fallback icons; P2 capture directly changed the table cursor without routing the video selection through SelectionSession. Result blocked.
2. Source plus `revised-smoke.png` opened together. Same viewport/density. Bottom secondary pages moved into More analysis, duplicate buttons removed, quantity selector widened, nearby display rounded with full-value model/tooltips preserved, capture changed to canonical sample selection (frame 12 / 0.400 s). Media thumbnail added. Remaining P2: dark fallback icons and oversized native popup indicator; remaining layout difference: tracking method still consumed inspector space. Result blocked.
3. Implemented native-icon silhouette tinting with public Qt painting, suppressed the extra popup indicator, moved tracking method to the library, and preserved its busy gates. Final capture is configured to normalize Retina output to 1488 × 1058 at 1x before comparison. Awaiting packaged result.
4. Opened `selected-a.png`, final `bundle-smoke.png` and `bundle-compact.png` together in one comparison input. The final app was executed from `/Users/leo.xu/Downloads/Neo-Tracker-studio-20261005/Neo-Tracker.app`. Main capture is 1488 × 1058 at 1x, compact capture is 1024 × 768 at 1x; native DPR was 2. Earlier P2 findings are resolved: readable monochrome icons, no oversized export indicator, visible quantity names and numeric values, three primary analysis tabs with secondary routes in a menu, one focus control, and frame/time agreement between video and data. No additional P0/P1/P2 fixes were required after this comparison.

## Final visual review

| Area | Result |
| --- | --- |
| Layout | Compact top command row; left media and tracking-method browser; central video; contextual right inspector; full-width lower plot with adjacent three-row data table. Splitters remain adjustable. |
| Typography | Native system font and Chinese glyphs, with weight-based hierarchy. Denser than the illustrative reference; user larger-font preference is inherited. No forced small pixel font in the desktop stylesheet. |
| Color and assets | Graphite panels, thin separators, blue selection/export, amber real-sample cursor and fit layer. Native/library icon shapes are tinted, not drawn as replacement artwork. No reference photo or mock numerical data is embedded. |
| Controls | Persistent import/run/export; busy cancellation is reachable. Calibration is an inspector category and fitting retains its existing detailed page, rather than reducing validated editors to decorative mock fields. |
| Responsive | At 1024 × 768 the inspector scrolls vertically, toolbar remains visible, and plot/table remain usable without horizontal inspector overflow. |
| Content | True frame 12, stored time 0.400 s, x approximately 108.13 px are aligned. Nearby values come from the canonical model, not rounded values fed back into calculations. |

Remaining P3 differences are documented adaptations: system-font density; solid material instead of a subtle glass gradient; three primary analysis tabs instead of two; retained engineering validation controls. No unsupported multi-object or zoom controls were invented for visual similarity.

## Functional verification

- `targeted-tests.log`: 48 tests passed (command routing, Chinese text, workflow gates, selection, workspace, plot semantics and white-background export).
- Additional fit-comparison test passed: retain the fit only when the identical primary series remains selected.
- Five focused layout/font/focus checks passed, including 1024 × 768 and inherited larger system fonts.
- Thirteen action-registry/source-review/nearby-model checks passed.
- Source Cocoa smoke passed tracking positions/times, ROI cancellation, fitting, CSV/NPZ/Markdown export and save/reopen equality. This is not yet frozen-bundle evidence.
- `bundle-smoke.json`: actual downloaded frozen app passed all ten checks, including its own NumPy/OpenCV/SciPy/PySide6 runtime paths. Native window exposed; temporary test media and test windows removed on completion. Signature integrity verified with `codesign --verify --deep --strict`.
- `bundle-smoke.log` contains a Qt font-alias warning and an IMK mach-port message; neither failed this run. No native accessibility hierarchy scan was performed.

## Boundaries

Do not claim a pixel-identical clone, measurement accuracy validation, comprehensive macOS accessibility acceptance, Developer ID signing or notarization. Existing advanced tools remain reachable; no unsupported multi-object creation, zoom tool or mock fit values were added to imitate reference-only controls.
