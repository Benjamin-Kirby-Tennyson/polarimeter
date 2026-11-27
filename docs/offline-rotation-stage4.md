# Simulated rotation and acquisition Stage 4

Date: 2026-11-27. Status: **SIMULATED software**, no installed-firmware compatibility
or physical readiness claim. The real serial/motor adapters remain locked.
This stage follows the [Stage 3 review](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7209634066).
The [canonical hardware context](hardware-integration-context.md) is unchanged.

## Working flow

Launch `polarimeter.py --gui --simulate`, open **Rotation lab**, then select
**Start 3-turn scan**. One explicit user action performs a framed HELLO,
configuration, explicitly simulated reference establishment, challenge, ARM and
finite SCAN. It does not automatically rearm after completion/cancellation/fault.
An explicit new Start creates a fresh simulated experiment, not a physical reset.
Hardware-locked mode cannot open the Rotation lab.

The demo requests 0–1080°, 30 deg/s and 60 deg/s², with a 180–900° collection
plateau and 45 s motion cap. These are **invented fixtures**, not the historical
25 deg/s experiment, TSINY settings or safe hardware defaults. Plant/controller
parameters remain those documented in [Stage 3](offline-simulation-stage3.md),
with explicitly increased simulated travel/duration limits for this finite scan.

The existing 0–360° Jones scan, manual controls, CSV behavior, USB metadata
panel and pointer check remain available. Pointer check is now inside Motor / USB.
No optical calibration or Stokes/DoP reconstruction was implemented.

## Review corrections and atomic state

SCAN is an allowlisted finite transaction with end angle, speed, acceleration,
duration, plateau start/end and speed band. Device checks verify a valid arm,
reference, directed bounded plateau, travel envelope and independent lease/time
limits. Requested speed/acceleration bounds do not imply identical actual motion.
Estimated overspeed, zero-speed outward starts near a limit, and travel during
coasting now have explicit tests. The predictor remains conditional on configured
deceleration/margins and delayed estimated velocity. A test deliberately
underestimates braking and crosses a limit while coasting: inhibition is not
mechanical containment. Re-zeroing is rejected during a travel fault; returning
inside the original mechanical frame is distinct from changing coordinates.

Reversal dwell resets when the requested direction changes or a crossing is
interrupted. The speed-command reversal policy is distinct from signed PI duty:
opposite duty may brake before dwell completes. This is a first-order model,
not TB6612FNG bridge truth-table or safe physical reversal implementation.

The harness binds every motion to boot/session/arm generation/request ID and
emits a terminal COMPLETED/CANCELLED/FAULT event once. STOP after completion
cannot reissue the previous completion. Protocol ACK is acceptance only.
Host-facing replies are amended to FINAL_HARNESS_CYCLE after predictor, PI and
settling checks; numeric errors inhibit before propagating. Plant integration
under old duty before a delayed call remains a simulation limitation, not a
proved firmware stopping deadline.

## Host and worker boundaries

`polarimeter_rotation.py` provides a framed in-memory endpoint and host adapter.
No transport-opening API exists. Both endpoints use a deliberately shared
virtual microsecond clock; a live host/ESP32 clock mapping is not implemented.
Response fields, version/mode, identities, nonce, sequence, time, ACK class/ID,
motion terminal and output/state consistency are validated. Retries retain exact
request bytes, not regenerated deadlines. ACK deadlines cannot revive at expiry.
Late/malformed/reset/disconnected status latches UNKNOWN and requests STOP;
later traffic cannot silently restore availability or claim physical stopping.

Queues are bounded: eight ordinary requests plus a priority STOP slot; at most
32 delayed response frames, eight host pending requests, eight response payloads
for duplicate checking and sixteen recent acknowledged IDs. Existing QWP1 frame
limits remain. A response/admission overflow faults/inhibits instead of growing
memory. These are simulated queue contracts, not UART bandwidth measurements.

`RotationWorker` owns model time, host exchanges, acquisition and file writes.
Tk only submits bounded requests/stop events and consumes a one-slot coalesced
snapshot queue. Plot history is capped at 1000 points and redraws at 5 Hz.
Back/Exit wait asynchronously for worker shutdown and file closure. Cancellation
immediately after Start is retained. Disk errors preserve their diagnostic and
UNKNOWN termination in the manifest. Tests deliberately leave Tk unpolled while
the worker advances, and close/cancel the active panel on a mapped desktop.

Virtual time advances in 5 ms steps and may run slower than wall time. A stalled
worker also stalls this simulated device clock; no independent hardware watchdog
or ESP32 execution-time guarantee follows from GUI-independent threading.

## Optical samples and provenance

The simulated detector integrates the existing Jones model against **optical
truth**, not commanded or encoder angle. Exposure length is 10 ms, sample period
20 ms, and delivery delay one sample period. A synthetic optical clock has
10 ms offset and 50 ppm drift; its explicit mapping has a 100 µs fixture bound.
Integer timestamp rounding is retained. A real detector clock/model is unknown.

Every exposure retains start/end optical timestamps, mapped extent/midpoint,
raw bracketing encoder counts/times, reference/motion identity, inferred angle,
simulated optical truth, velocity and timing-angle uncertainty contribution.
Piecewise-linear truth interpolation with trapezoidal intensity integration is
the chosen numerical approximation. Required brackets, steady directed plateau,
reference lifetime and sequence continuity are checked, including brackets
outside an exposure's endpoints. Missing mapping, incorrect drift, gaps, reversal
and transition windows are flagged rather than silently called valid.

Raw encoder/status CSV and optical CSV are streamed separately under
`data/simulated-rotation/`, with explicit SIMULATED mode/filenames. A JSON session
manifest records parameters, phase, reason, metrics and reconstruction PENDING.
No files are created by merely opening the panel. A scan cancelled before any
exposure can leave an empty optical file; it is not an acquisition success.
The raw history used for interpolation is bounded to 128 points; the full raw
stream remains on disk. No hardware records enter these simulation loggers.

## Executed verification and metrics

On the Pi desktop:

```bash
POLARIMETER_GUI_TESTS=1 .venv/bin/python -B -m unittest discover -q
.venv/bin/python -B -m polarimeter_rotation_session --simulate --data-dir data
```

The complete opt-in suite passed **153 tests, zero failures/errors/skips**,
including ten mapped Tk tests. New tests cover cancellation, lost/delayed/
duplicate/corrupt replies, boot/session/time/schema checks, host/device timeouts,
queue bounds, exposure brackets/gaps/reversal, clock offset/drift, disk failure,
worker shutdown and an end-to-end multi-revolution acquisition. Synthetic GUI
events do not establish physical finger input or instrument behavior.

Measured **simulation-only** end-to-end fixture results:

| Metric | Result |
| --- | --- |
| Virtual scan duration | 38.285 s |
| Valid / flagged exposure records | 1198 / 2 transition-edge windows |
| Inferred plateau coverage | 718.2 deg of the 720 deg planned interval |
| Mean inferred / true optical speed | 30.0 / approximately 30.0 deg/s |
| Inferred plateau speed ripple | 0 deg/s at this fixture's sample phases |
| Truth ripple | Below 1e-8 deg/s numerical residue; not physical precision |
| Mean inferred minus optical fast-axis angle | Approximately -4.99985 deg |
| Intensity RMS versus model at inferred angle | Approximately 0.06144 uncalibrated a.u. |

The nearly 5° phase difference is the deliberately uncalibrated optical zero
fixture. It is preserved in Jones acquisition and reported separately, rather
than hidden by substituting encoder or commanded angle for optical truth.
No Stokes, DoP or calibrated power accuracy is claimed.

## Publication and remaining work

The feature snapshot includes the previously local Tk/Jones GUI and offline
Stages 1–4 because remote main did not contain that baseline. Publication uses
an isolated feature branch based on remote main; it preserves the canonical
handoff and local working checkout. Nothing is merged into main automatically.

Next: independent review, then an ESP32 target implementation/build plan.
No firmware image/build, new baud choice, live serial open or GPIO output is
part of this stage. Original firmware identity, pinout/electrical levels,
encoder/optical calibration, motor/driver current/thermal suitability,
independent energy interruption and commissioning remain unresolved.
