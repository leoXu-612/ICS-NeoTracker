# G0 — Current Layout and Physics Workspace Design Plan

## Current layout

```text
NeoTrackerWindow
┌──────────────────────── application toolbar ────────────────────────┐
├──────────────────────── QSplitter(horizontal) ──────────────────────┤
│ Preview canvas + source-time transport │ fixed-min 450 px sidebar  │
│                                        │ Media/Tracking/Review/...  │
└────────────────────────────────────────┴────────────────────────────┘
```

The sidebar has a 450 px minimum width and a 625 px minimum tab height. Results, diagnostics, run/edit history, and Signal controls currently compete inside that column. This is usable for workflow configuration but unsuitable for a wide physical-series table or a true-time plot, especially at 1024x768.

## Compact design plan

Subject: a desktop physics laboratory workbench for students and experiment reviewers. Its single job is to keep video evidence, measured values, and fitted claims aligned to the same true-time observation.

### Tokens

| Role | Token |
| --- | --- |
| Lab canvas | `#F3F5F6` |
| Instrument paper | `#FBFCFC` |
| Instrument ink | `#20272C` |
| Datum blue | `#2F6F9F` |
| True-time cursor | `#C55232` |
| Invalid / caution | `#8A641C` |

- Display role: **Avenir Next Condensed**, semibold, used only for the compact workspace title and quantity labels.
- Body role: **Helvetica Neue**, matching the existing shell.
- Data role: **SF Mono**, used for frame/time/value cells and fit parameters so decimal structure remains inspectable.
- Layout: retain the existing toolbar and horizontal shell; wrap that shell in a vertical splitter and add one collapsible bottom instrument tray.

```text
┌────────────── existing shell, unchanged hierarchy ───────────────┐
│ Project/config       Video evidence                Inspector      │
├─ PHYSICS / true time 2.417 s ────────────────────────────────────┤
│ Data | Plot | Fit | Diagnostics | Runs | Edits | Signal      [⌃] │
│ ─────────────── shared cursor rail ────────────────────────────── │
│ selected page content                                             │
└───────────────────────────────────────────────────────────────────┘
```

Signature: one vermilion true-time cursor is the sole emphatic element. It has the same time text and semantic position in Video, Data, Plot, and Fit context, making synchronization visible rather than decorative.

Motion is limited to the splitter/collapse transition supplied by Qt and direct cursor updates. No ambient animation, gradients, floating cards, or ornamental chart effects are introduced.

## Self-critique and revision

The first concept used a dark oscilloscope panel with glowing traces. That would be visually distinctive but would read as a generic instrumentation theme, conflict with the existing light macOS shell, increase contrast/Retina risk, and spend visual emphasis on the whole tray. It was rejected.

The revised direction keeps the current shell palette and makes only the shared true-time cursor memorable. The pale instrument-paper surface, condensed quantity labels, mono numeric data, explicit `RAW` / `FILTERED` / `DERIVED` text, and gap labels come from the physics-review task rather than a generic dashboard. Color never carries validity or provenance alone.

## Build constraints

- No global Shell redesign and no new plotting dependency.
- The bottom tray is a `QSplitter` child with an explicit collapse affordance, remembered bounded height, and a minimum usable content height.
- `Data` is a `QTableView` backed by `QAbstractTableModel`; no per-cell widgets.
- `Plot` paints only precomputed NumPy envelopes bounded by viewport width, preserving endpoints, extrema, selected/range samples, and invalid gaps.
- All statuses have visible text and accessible descriptions; keyboard focus and left/right plot navigation are first-class.
