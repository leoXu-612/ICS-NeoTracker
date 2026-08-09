# ICSTracker App Icons

Recommended macOS app icon:

- `ICSTrackerWaveROIPoint.icns`

Source and preview assets:

- `icstracker-app-icon-wave-roi-point-1024.png`: 1024 px RGBA icon combining a complex cyan waveform, gold ROI, and one small red particle placed directly on the right-hand waveform crest. It contains no text or identifying marks.
- `icstracker-app-icon-wave-roi-point-preview.png`: large visual preview for the recommended waveform/ROI/particle edition.
- `icstracker-app-icon-wave-roi-point-size-check.png`: 16/32/64/128/256 px readability check with protected particle visibility at small sizes.
- `ICSTrackerWaveROIPoint.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `icstracker-app-icon-wave-roi-1024.png`: 1024 px RGBA icon using only a complex cyan waveform and a gold ROI with four corner handles. It contains no text, letters, numbers, point marker, badge, crest, or school identifier.
- `icstracker-app-icon-wave-roi-preview.png`: large visual preview for the recommended text-free edition.
- `icstracker-app-icon-wave-roi-size-check.png`: 16/32/64/128/256 px readability check with optically strengthened ROI artwork at small sizes.
- `ICSTrackerWaveROI.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `icstracker-app-icon-neo-wave-v2-1024.png`: 1024 px RGBA icon with an exact Neo-Tracker wordmark, complex beat waveform, and a single clean ROI frame. It contains no ICC, RDFZ, crest, or school name.
- `icstracker-app-icon-neo-wave-v2-preview.png`: large visual preview for the recommended Neo-Tracker wordmark edition.
- `icstracker-app-icon-neo-wave-v2-size-check.png`: 16/32/64/128/256 px readability check; the 16 px optical version uses the related `NT` monogram.
- `ICSTrackerNeoWaveV2.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `icstracker-app-icon-wave-icc-v2-1024.png`: 1024 px RGBA icon with an enlarged RDFZ/ICC centerpiece, complex beat waveform, and expanded ROI brackets.
- `icstracker-app-icon-wave-icc-v2-preview.png`: large visual preview for the recommended ICC-forward edition.
- `icstracker-app-icon-wave-icc-v2-size-check.png`: visual QA of the optical-size-specific 16/32/64/128/256 px artwork.
- `ICSTrackerWaveICCV2.iconset/`: macOS iconset with separately composed small-size ICC artwork instead of mechanical downscaling.
- `icstracker-app-icon-wave-icc-1024.png`: 1024 px RGBA icon with a multi-harmonic beat waveform, centered RDFZ/ICC red-gold tracking medallion, and ROI brackets.
- `icstracker-app-icon-wave-icc-preview.png`: large visual preview for the recommended complex-waveform edition.
- `icstracker-app-icon-wave-icc-size-check.png`: 16/32/64/128/256 px readability check for the recommended complex-waveform edition.
- `ICSTrackerWaveICC.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `icstracker-app-icon-icc-phase-1024.png`: 1024 px RGBA icon with a centered, locally typeset RDFZ/ICC red-gold tracking medallion, damped-oscillator phase portrait, and ROI brackets.
- `icstracker-app-icon-icc-phase-preview.png`: large visual preview for the recommended RDFZ/ICC edition.
- `icstracker-app-icon-icc-phase-size-check.png`: 16/32/64/128/256 px readability check for the recommended RDFZ/ICC edition.
- `ICSTrackerICCPhase.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `icstracker-app-icon-phase-1024.png`: 1024 px RGBA icon built around a damped-oscillator phase portrait, tracked particle, and ROI brackets. It deliberately contains no text or crest so it remains readable in the Dock.
- `icstracker-app-icon-phase-preview.png`: large visual preview for the recommended ICSTracker icon.
- `icstracker-app-icon-phase-size-check.png`: 16/32/64/128/256 px readability check for the recommended ICSTracker icon.
- `ICSTrackerPhase.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `icstracker-app-icon-icc-1024.png`: 1024 px RGBA icon with damped-oscillation tracking, ROI selection, a clean ICC seal, straight text labels, and RDFZ-inspired red/gold accents.
- `icstracker-app-icon-icc-preview.png`: large visual preview for the recommended ICSTracker icon.
- `icstracker-app-icon-icc-size-check.png`: 16/32/64/128/256 px readability check for the recommended ICSTracker icon.
- `ICSTrackerICC.iconset/`: macOS iconset source used to build the recommended `.icns`.
- `neo-tracker-app-icon-damped-1024.png`: 1024 px RGBA icon with damped-oscillation trajectory, ROI selection, and tracked mass point.
- `neo-tracker-app-icon-damped-preview.png`: large visual preview for the recommended icon.
- `neo-tracker-app-icon-damped-size-check.png`: 16/32/64/128/256 px readability check for the recommended icon.
- `NeoTrackerDamped.iconset/`: macOS iconset source used to build the damped-motion `.icns`.
- `neo-tracker-app-icon-roi-1024.png`: 1024 px RGBA icon with MATLAB-style tracking ROI and mass-point marker.
- `neo-tracker-app-icon-roi-preview.png`: large visual preview.
- `neo-tracker-app-icon-roi-size-check.png`: 16/32/64/128/256 px readability check.
- `NeoTrackerROI.iconset/`: macOS iconset source used to build the `.icns`.

The earlier ICC seal version is kept as `ICSTrackerICC.icns`. The earlier damped-motion version is kept as `NeoTrackerDamped.icns`. The earlier parabolic ROI version is kept as `NeoTrackerROI.icns`. The earlier non-ROI version is kept as `NeoTracker.icns` and related `neo-tracker-app-icon-*` files.

Packaging example:

```bash
pyinstaller --windowed --name ICSTracker --icon assets/app-icon/ICSTrackerWaveROIPoint.icns -m neo_tracker
```
