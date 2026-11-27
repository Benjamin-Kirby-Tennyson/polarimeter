# Offline controller model stage 2

Historical Stage 2 report. [Stage 3](offline-simulation-stage3.md) now supplies
count-derived feedback/reference epochs, readiness bands/dwell, cause-specific
clearance, sequence-exhaustion rules and a separate plant/trajectory/PI harness.

Status: **SIMULATED REFERENCE SOFTWARE** for proposed protocol revision 0.2.
This does not identify or implement installed ESP32 firmware. It adds no live
serial, GPIO, motor driver, background process or GUI behavior. Existing physical
adapters remain locked. Timing, configuration and feedback in tests are fixtures.

Review addressed: [ChatGPT Stage 1 review](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7208926729).
Published implementation report and source: [Stage 2 response](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7209079825).
Design: [revision 0.2](proposed-motor-protocol-rev02.md).
Reported wiring remains in the unchanged [canonical context](hardware-integration-context.md).

## Implemented behavior

- [FrameMessage](../polarimeter_protocol.py) retains immutable validated frame
  and payload bytes. Message access returns a defensive fresh dictionary. The
  existing dictionary-returning APIs remain compatible; new model callers use
  `FrameDecoder(..., preserve_payload=True)`. Exact retry identity is independent
  of JSON re-encoding, whitespace normalization or CRC authentication claims.
- [OfflineController](../polarimeter_controller.py) is caller-polled with explicit
  Timing, injected SimulatedFeedback and an in-memory SimulatedOutput. HARDWARE
  envelopes and physical output callbacks are not supported.
- A cycle accepts at most eight events and returns at most eight snapshots.
  Fault/expiry, schema errors, payload conflicts, RX/TX admission and STOP are
  evaluated before ordinary state mutations. STOP always inhibits, including
  repeats/stale identity, and cannot clear faults. DISARM precedes ordinary
  requests. Multiple ordinary state changes in one batch are rejected.
- STOP/fault suppression also prevents GET_STATUS from issuing a challenge in
  that cycle. TX capacity is reserved before enabling simulated output. These
  bounds do not implement UART queues or prove target scheduling deadlines.
- Configuration is atomic and requires fresh stationary simulated evidence.
  ARM requires current configuration, calibration-matching reference feedback
  and a fresh one-use challenge, obtained with GET_STATUS. Revocation invalidates
  the challenge/lease. Changed count scaling cannot reuse a calibration identity.
- Timing uses checked unsigned 64-bit integer microseconds. Equal timestamps
  are valid; backwards time or horizon overflow faults. Equality expires lease,
  request, challenge, feedback and motion timers. A cycle gap faults when it is
  greater than the maximum permitted interval. Simultaneous causes are latched.
- A newer valid heartbeat can shorten a lease but never extend a motion-duration
  or travel cap. Replayed heartbeats cannot renew. Exact retries never repeat
  ordinary state changes; eight cached outcomes retain exact payload bytes and
  old evicted IDs are rejected. Conflicting bytes fault before batch enables.

## Bootstrap and schema decisions

Source code defines exact command field allowlists; booleans are not accepted
as integer/physical-number inputs. Decimal IDs and hexadecimal identities are
strictly bounded/canonical. Unknown mode/type/fields cause a protocol fault.

HELLO/STOP do not require established session/deadline. HELLO uses null identity
fields plus a nonce, echoed in its reply. Every HELLO, including a repeat,
conservatively inhibits and rotates the session before accepting further work;
old feedback is discarded. This chooses session rotation over revision 0.2's
optional disarmed handshake retry cache. Recent identity collisions or identity
source failure fault; random IDs are not a guarantee of impossible collisions.

GET_STATUS requires current identities but no deadline. HEARTBEAT has a separate
sequence space: request_id equals heartbeat_seq and does not advance ordinary
request IDs. STOP bypasses replay suppression. Other requests advance per session.

Initial CONFIGURE needs fresh raw-count/zero-velocity evidence but not an already
valid angular reference. ARM later requires reference-valid feedback matching the
accepted calibration. Tests inject this simulated evidence explicitly; no encoder
pin/supply/CPR or physical readiness is inferred. Physical commissioning/homing
is absent, and injecting simulation data cannot bypass the real hardware lock.

Implemented subset: HELLO, GET_STATUS, CONFIGURE, ARM, HEARTBEAT, MOVE_TO, STOP,
DISARM, CLEAR_FAULT. SET_VELOCITY is rejected. MOVE_TO only marks RUNNING and a
simulated boolean active: no trajectory, PWM, PI/PID or automatic arrival is
implemented. Requested target, injected count/angle and sample/device timestamps
are separate. Motor counts are not direct optical fast-axis measurement.

## Verification and remaining stages

Reproduce with `.venv/bin/python -B -m unittest tests.test_protocol tests.test_controller -v`.
Result: **45 passed** (14 codec and 31 controller tests). Full discovery:
**96 discovered, 89 passed, seven opt-in GUI tests skipped**, zero failures/errors.
Earlier GUI passes are historical and were not rerun for these independent modules.

Tests cover raw-byte retries/conflicts, fault/STOP permutations, MOVE→STOP,
STOP→ARM, DISARM/HELLO precedence, heartbeat equality/shortening/replay, queue
admission, silent partial-frame timeout, reset, clock regression/overflow,
feedback/reference loss, calibration changes, travel/duration, fault recovery,
identity failure, immutable events and absence of serial/heavy-package imports.

The caller must poll parser/model in silence. The codec retains its float-second
fixture API; controller time is integer microseconds. No worker, UART backlog
admission, measured Tc+J+Ti deadline or independent watchdog is supplied. A clock
that never advances cannot be diagnosed from equal injected timestamps alone.

Future stages: simulated encoder/trajectory/PI plant and measured settling,
host adapter, then separately reviewed actual firmware implementation/builds.
Original-source recovery, electrical suitability, calibration, independent motor
energy interruption and live commissioning remain unresolved. No serial open,
flash, movement or physical bench test is authorized by these results.
