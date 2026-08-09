# Kinematics Contract v0.3

## Status and provenance

- Contract owner: Codex single-writer P0 stage.
- Immutable v0.2 base tag: `v0.2.0-alpha.1`.
- `V0_2_BASE_SHA`: `aa3a2eb4a25a154e96f1f7f2bc9d1b315b8b7186`.
- The shared `KINEMATICS_CONTRACT_SHA` is the commit containing this document;
  both feature branches must be created directly from that commit.

## Invariants

`SampleSeries` is frame aligned. `frame_indices`, `time_s`, `values`, and
`valid_mask` always have equal length and are detached read-only NumPy arrays.
Missing or lost observations remain in place with `valid_mask=False`; they are
not deleted or silently interpolated. Frames are non-negative unique integers.
Times marked valid are finite and strictly increasing, and values marked valid
are finite.

Every series, fit request, fit result, and bundle carries a non-empty
`source_revision`. `require_current_revision` and bundle construction reject
stale data rather than applying it. Processing metadata is detached, deeply
immutable, bounded, JSON compatible, and has readable deterministic text.

## Time and gaps

All derivation uses stored `TrackerResult.time_s`. Nominal FPS must never be
used to reconstruct a time axis. `nonuniform_finite_difference` supports true
nonuniform time. The only v0.3 gap policy is `split`; no method may cross a
missing segment by default.

`savgol_uniform` is permitted only when each contiguous valid segment satisfies
the configured relative cadence tolerance. A failing cadence check is an
explicit error. Silent resampling is outside v0.3.

## Units

Units are fields, not decorations embedded only in names. Supported first-pass
rules are:

| Quantity | Unit |
| --- | --- |
| position | `m`, `cm`, or `px` |
| velocity | `<position>/s` |
| acceleration | `<position>/s²` |
| angle | `rad` |
| angular velocity | `rad/s` |
| angular acceleration | `rad/s²` |

An unknown source unit remains the empty string. UI code must render that as
“unit unavailable”; it must not infer a unit.

Fit coefficient order is stable:

- linear: `slope`, `intercept`;
- quadratic: `a`, `b`, `c` for `a*t² + b*t + c` (physical acceleration is
  `2*a`, not `a`);
- exponential: `amplitude`, `rate`, `offset`;
- sinusoidal: `amplitude`, `omega`, `phase`, `offset`.

## Fit contract

Fit ranges are finite and strictly increasing. Initial parameters and bounds
are named, finite values. Successful results require finite parameters,
metrics, predictions, and residuals on their fit mask. `sample_count` exactly
matches that mask. Non-success terminal states are `failed`, `cancelled`,
`unavailable`, and `stale`; they remain explicit and carry a user-readable
message.

Linear and quadratic implementations must remain available without SciPy.
Exponential and sinusoidal implementations may return `unavailable` when the
optional science dependency is absent, but importing `neo_tracker.kinematics`
must still succeed.

## Serialization and large inputs

Contract `to_dict` payloads are strict JSON-safe wire objects. Numeric arrays
use `{dtype, shape, data_b64}` instead of per-sample Python lists. This keeps a
100,000-sample contract payload contiguous and avoids recursive row/container
amplification. Decoding validates dtype, rank, exact byte length, base64, and a
512 MiB per-array limit before constructing a dataclass. Unavailable scalar fit
metrics are encoded as JSON `null` and restored as `NaN`; non-finite array
payloads remain inside the typed binary envelope rather than non-standard JSON.

Project schema v3 will persist analysis definitions, ranges, view state, and
provenance—not derived 100k arrays. CSV/NPZ/Markdown export is an engine-owned
operation and must remain atomic and safe (`allow_pickle=False` for NPZ reads).

## Parallel ownership boundary

The engine implements `SeriesBuilder`, `DerivativeOperator`, and `FitOperator`.
The Application/UI layer consumes only immutable snapshots and these protocols;
it does not perform numerical fitting. Neither parallel branch may modify
`types.py` or `protocols.py`. A contract defect must be proposed as an RFC and
resolved by the integrator.
