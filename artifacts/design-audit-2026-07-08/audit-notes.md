# Neo-Tracker Desktop UI Audit

## Scope

Local PySide6 desktop flow captured on 2026-07-08:

1. Empty project start: `01-empty-project.png`
2. WAV media loaded: `02-wav-processing-source.png`
3. Tracked synthetic marker review: `03-tracking-review.png`

## Findings

1. Empty project state
   - Health: improved.
   - The preview now says "video or WAV" instead of only "video", matching the current media support.
   - The right sidebar is visually stable, but the task list is still a large empty region by design.

2. WAV media state
   - Health: good.
   - The app distinguishes audio from video with sample rate, samples, channels, and disabled tracking/playback.
   - The audio workflow now lands under the English `Signal` tab, removing the mixed-language tab label.

3. Tracking review state
   - Health: good with one remaining density tradeoff.
   - Review actions, overlays, result table, confidence chart, and preview overlays are visible in one work surface.
   - The result table can horizontally scroll when calibrated state columns are present; this is acceptable for dense scientific review, but future grouping or column pinning would help long sessions.

## Accessibility Notes

- Key action buttons now expose tooltips that describe the action outcome.
- Disabled controls are visually distinct, and status chips summarize the current task state.
- Screenshot-only review cannot prove keyboard order, screen reader names, or contrast compliance.

