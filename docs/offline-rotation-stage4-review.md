# Stage 4 independent review follow-up

2026-11-27. **SIMULATED software only.** Responds to
[ChatGPT review of commit 2d9b921](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7224388671)
on [draft PR #2](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/pull/2).
The review was a targeted source review, not a complete audit or independent
execution. This report records Codex's subsequent local tests. Remote main and
hardware locks remain unchanged; no physical port opened, firmware flashed,
GPIO driven or motor operated. Canonical hardware context is preserved.

## Corrections

1. **Terminal recovery and deadlines.** The in-memory endpoint retains one
   terminal record (boot/session/arm/motion/time/outcome). Subsequent status and
   session-safe GET_STATUS recover the same record after a dropped terminal
   frame; a new session clears it and a later motion can supersede it. STOP after
   completion acknowledges inhibition without re-emitting a new completion.
   Duplicate identical terminal records are accepted, conflicting records fail.
   Lost/unrecoverable inactive state fails to UNKNOWN; live packets cannot keep
   setup or a scan alive indefinitely. Each setup phase has a 500 ms cap and
   scanning has the explicit duration plus 200 ms host cap. These are simulator
   fixture policies, not verified physical deadlines.
2. **Identity and admission.** Fresh experiment, controller boot/session and
   host nonce identities use UUIDs; deterministic identity factories remain
   injectable fixtures. Host checks arm generation, current reference/motion,
   legal ACK/state/phase relationships, terminal inactivity, fault consistency
   and terminal identity. Faults cannot be overwritten by a successful terminal.
   Heartbeat ACKs have bounded tracked IDs and 150 ms acknowledgment deadlines;
   rejection/missing ACK fails to UNKNOWN even with other live status traffic.
   STOP at RX capacity still inhibits; absence of ACK does not establish a known
   stopping state. Virtual-time/in-memory checks are not transport guarantees.
3. **Independent detector physics.** Intensity and true optical speed use actual
   simulated exposure times and a separate bounded truth timeline. Host mapping
   affects inferred association only; an in-tolerance wrong mapping leaves the
   detector measurement identical. Delayed status cannot rewind the independent
   physics timeline. Nonfinite/zero/reversed mapped spans flag
   rather than divide. Acceptance requires brackets over the entire
   uncertainty-expanded interval, with sequence/identity/state/steady-direction
   checks. Timing-angle error uses the configured speed ceiling over the
   exposure, not just one sampled velocity.
4. **Provenance and integrity.** Optical CSV now carries experiment, boot,
   session, arm, motion, reference, config/calibration identities, actual/mapped
   times, estimated/true endpoints, current raw sequence and full bracket
   sequences/identity. Identity/time discontinuity resets interpolation history.
   Raw intensity survives association failure; inferred angle is invalidated.
   Expected/received/valid/flagged/missing exposures, valid-segment gaps, valid
   midpoint span and contiguous midpoint coverage are separate metrics. Coverage
   is between valid midpoints, not full exposure-edge or planned-angle coverage.
5. **Lifecycle and disk finalization.** Each worker session resets publication
   timing, immediately publishes its identity and clears old GUI measurements.
   Start/final publication share a lifecycle lock; busy clears after final
   snapshot publication. GUI ignores snapshots of a previous experiment.
   Data streams flush/close before atomic manifest replacement. Flush/close
   failures record UNKNOWN/incomplete data and preserve diagnostics; a manifest
   write/rename failure leaves no final success manifest (a `.json.tmp` may
   remain as partial evidence). Written encoder/exposure counts are retained.
   Header/init failure closes open handles and publishes UNKNOWN. No claim of
   fsync/power-loss durability or physical watchdog coverage is made.

## Executed verification

```bash
POLARIMETER_GUI_TESTS=1 .venv/bin/python -B -m unittest discover -q
```

**176 tests passed, zero failures/errors/skips**, including **eleven mapped Tk
GUI tests**. Targeted tests include exact lost/delayed/duplicate/conflicting
terminal, STOP after completion, setup/scan deadlines, rejected/missing heartbeat
ACK with live status, wrong generation/motion/disarmed state, STOP at capacity,
clock mapping independence, delayed-status/truth-history isolation, malformed
mapping, full uncertainty brackets,
identity/time discontinuity, missing samples/coverage, final flush/manifest/header
failure, consecutive starts, immediate cancellation, stale GUI snapshots and
shutdown without polling Tk. Synthetic GUI events do not verify physical touch.
The final opt-in run completed in 26.844 seconds on this Pi.

The Python side of shared target vectors runs in this suite. C++ target/native
runners were **not compiled or executed**; no ESP32 toolchain was installed.
Source syntax, local documentation links/code fences and whitespace are checked
separately before publication. Earlier 153-test Stage 4 results remain historical.

## End-to-end fixture results

Accelerated acquisition CLI completed in 38.285 s virtual time with 1200 expected
and received exposures: 1198 valid, two flagged transition windows, zero missing,
zero gaps between valid segments. Both valid midpoint span and contiguous
midpoint coverage are 718.2° for this gap-free fixture. Counts do not certify
real sampling, timing, calibration or physical control.

Separate [Stokes analysis](simulated-stokes-analysis.md) fit the streamed optical
CSV using explicit analyzer 0° and simulated fast-axis zero +5°. The input Jones
fixture is linear x, true Stokes [1,1,0,0].

| Fit angle source / explicit zero | Normalized [S1,S2,S3] | DoP | Residual RMS (a.u.) |
| --- | --- | --- | --- |
| Inferred +5° fixture calibration | [0.999986, −0.000222, approximately 0] | 0.999986 | 3.54e−8 |
| Inferred, uncalibrated 0° | [0.912067, −0.332195, approximately 0] | 0.970680 | 3.54e−8 |
| Truth oracle (test-only substitution) | [0.999986, approximately 0, approximately 0] | 0.999986 | 7.42e−16 |

All fits have rank four and condition approximately 3.61. A low residual alone
**does not detect optical-zero bias**. The small oracle DoP deviation comes from
detector trapezoidal integration versus uniform-angle harmonic integration;
inferred-angle residuals also reflect quantization/timestamp association.
These ideal/noise-free fixture numbers are not physical precision estimates.
Known-state noisy/gapped tests and nonphysical DoP flagging are separate.
Normal fitting never reads truth fields; oracle substitution occurs only in
explicit verification code. Acquisition manifests still say reconstruction
PENDING until the separate analysis command is run.

## Remaining work

Independent re-review of these changes; full strict JSON/controller target
conformance; SDK/toolchain build on a separate provisioned machine. The
[ESP32 target plan](esp32-target-build-plan.md) and compile-only mock skeleton
are separately reviewable and remain uncompiled. No live adapter is added.
Real clock synchronization, installed firmware identity, encoder electrical
pinout/count calibration, optical reference/instrument calibration,
current/thermal limits, reversal/bridge implementation, independent energy
isolation and separately authorized bench commissioning remain outstanding.
