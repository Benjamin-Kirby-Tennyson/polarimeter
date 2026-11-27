# Proposed motor protocol revision 0.2

Status: **PROPOSED OFFLINE DESIGN**. This revision supersedes conflicting
protocol/state/timing wording in [revision 0.1](proposed-motor-protocol.md).
The installed firmware remains UNKNOWN. The Python frame codec/parser and a
limited [simulation-only controller model](offline-controller-stage2.md) have
been implemented. No ESP32 firmware, live transport or GUI connection is implemented.
The latest [Stage 3 report](offline-simulation-stage3.md) records implementation
clarifications and the explicitly simulated plant/trajectory/PI results.
No source recovered from the board or historical sketch is implied.

Review source: [ChatGPT / CAD_QWP revision 0.1 review](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7208801717).
Published design, source and test review copy: [Rev 0.2 response](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7208911678).
Hardware history and pin mapping remain in the unchanged
[canonical context](hardware-integration-context.md). This document resolves
software semantics while leaving physical settings unset. Review acceptance
does not authorize live serial, flashing, GPIO output or motor movement.

## Normative event and state rules

The future control engine processes events in this order for each bounded
cycle: independent inhibit or detected fault; valid STOP; DISARM or lease
expiry; session establishment; configuration/ARM/normal motion requests.
New faults are latched before STOP processing. STOP always requests output
inhibition and revokes arming, but never clears a latched fault.

| Event | State change and side effects |
| --- | --- |
| Reset / boot | DISARMED; new boot identity; invalidate session, arming, trajectory, pending requests, count continuity and reference; request reviewed inactive outputs |
| Detected fault or independent inhibit | FAULT from every state; inhibit outputs, cancel pending/active motion, revoke arm generation, latch all applicable causes |
| Valid STOP | Reassert inhibition on every receipt, including duplicates; clear queued motion, revoke arming; remain FAULT if faulted, otherwise STOPPED, including before HELLO |
| DISARM | Same inhibition/revocation; remain FAULT if faulted, otherwise DISARMED |
| Lease expiry | FAULT with communication-timeout cause; do not wait for a host request or ACK |
| New HELLO in any state | Inhibit/revoke before replacing session; fault remains latched; cancel old requests and reject all old session traffic; new session starts DISARMED or FAULT |
| CONFIGURE | Accept only in DISARMED, CONFIGURED or STOPPED with stationary feedback; validate atomically, invalidate old reference when its calibration changes, enter CONFIGURED without arming |
| ARM | Accept only in CONFIGURED or STOPPED, with complete approved configuration, valid reference/fresh feedback and no faults; generate a new arm generation and enter ARMED with finite lease |
| MOVE_TO / SET_VELOCITY | Accept only in ARMED with current session/arm/config/reference and valid deadline/limits; start one trajectory and enter RUNNING |
| Normal trajectory completion | Require fresh in-band angle and velocity for the approved settling interval; inhibit routine drive, revoke arm generation and enter STOPPED |
| CLEAR_FAULT | Require causes absent and explicit request; enter DISARMED, retain invalid-reference status where applicable; never arm or restart |

Configuration remains available after routine STOP/completion, but ARM is
revoked. The first revision supports one bounded movement per explicit arm;
multi-segment scans are not supported by automatic host rearming. A future
explicit bounded scan plan could be reviewed separately. State transitions do
not certify mechanical stationarity, electrical isolation or driver behavior.

## STOP acceptance, priority and evidence

A STOP must be a complete CRC-valid QWP1 frame with version 1, matching mode,
type STOP and exactly these envelope fields: v, mode, type, request_id, boot_id,
session_id. IDs obey the types below; boot/session may be null for STOP.
No ARM, deadline or successful HELLO is needed. Stale, wrong or missing
boot/session values do not prevent inhibition; they are flagged in the response.
Unsupported versions, unknown fields, wrong mode, damaged framing/CRC or invalid
field types are not interpreted as STOP. While armed, protocol errors instead
latch a communication fault and request inhibition. CRC is not authentication;
an untrusted sender can cause denial of service, and this design provides no
hostile-peer motion authorization mechanism.

STOP bypasses motion/request queues and the dedup cache after validation. A
duplicate STOP reasserts inhibition and returns current state, not cached
success. TX backpressure, ACK allocation and telemetry cannot precede inhibition.
If a simultaneous fault exists, report FAULT with stop requested. Before HELLO,
the device may emit a bounded STOP_STATUS with current boot ID and no session.
The host shows stopping status UNKNOWN after ACK/telemetry loss, including when
firmware was expected to have inhibited. An ACK is not proof of energy removal.

Let Tc be the approved maximum interval between safety checks, J the measured
worst-case scheduling jitter, and Ti the measured maximum time to apply the
reviewed inactive outputs. From the final LF arrival of a complete valid STOP,
require software inhibition within Tc + J + Ti, including parser scheduling.
The design must reserve a complete maximum-frame parser budget per such cycle,
even if input is drained in smaller chunks. The actual numerical bound remains
unset and must be demonstrated; it is not a guarantee made by the Python codec.
From loss of fresh valid heartbeat acceptance, bound inhibition by Lmax + Tc +
J + Ti, where Lmax is the approved maximum lease horizon. Physical stopping
time/distance and independent energy interruption are distinct measured bounds.

## Device-time leases and motion limits

Device time is unsigned 64-bit monotonic microseconds relative to boot, encoded
as a canonical decimal string. At overflow or inability to advance time, fault
and require a fresh boot/session; modular wrap is not silently accepted.
Configuration provides explicit positive microsecond limits Lmax (heartbeat
horizon), Wmax (motion/request validity horizon), Fmax (feedback age), Tc
(control/safety interval), Pmax (incomplete-frame age) and Mmax (motion duration).
No timing value is a physical default; absent values block ARM.

For heartbeat sequence h, received at device time t, accept renewal only with
current boot/session/arm generation, h strictly newer than the last accepted
heartbeat, and t < deadline <= t + Lmax. Set the lease endpoint to the supplied
deadline, never t + a lease duration. A duplicate, replayed, expired or overly
future heartbeat cannot renew. A delayed new sequence can only renew to its
original unexpired deadline, never gain extra time from its delayed arrival.
Check existing expiry before processing renewal; at t >= lease endpoint the
lease is expired and cannot be revived by a packet at that same instant.

Host clock mapping must bound device-time uncertainty using a time exchange
and measured transport asymmetry budget; no synchronized-clock claim follows
from a serial read. Schedule expiry/commands within a conservative interval
under that uncertainty. If no defensible bound is available, do not arm. The
device still enforces its own deadlines independently of the host estimate.

Each motion has its own immutable maximum duration and travel envelope,
validated at acceptance: reject beyond configured Mmax/travel limits. Heartbeat
does not extend the motion end deadline. Expiry, exceeded travel, stale feedback
(age >= Fmax), control deadline miss or lost count continuity faults separately.
The incomplete-frame timer Pmax starts at its first byte; partial byte arrival
does not renew it. Software inhibition must not depend on frame timeout alone.

## IDs, exact types and retries

| Field | Normative proposed type / rule |
| --- | --- |
| v | JSON integer exactly 1; bool is not an integer |
| mode | String SIMULATED or HARDWARE, exactly matching the endpoint; no automatic fallback |
| type | Exact uppercase member of the message set in revision 0.1; message-specific fields allowlisted before dispatch |
| request_id | Canonical decimal string 1..18446744073709551615; one increasing host-request sequence per session, except STOP bypasses monotonic rejection |
| boot_id | Exactly 32 lowercase hexadecimal characters, fresh unpredictable 128-bit boot value; null only for HELLO or STOP |
| session_id | Same string shape, newly assigned device value for each new HELLO exchange; null only for HELLO or STOP |
| arm_generation | Session-scoped decimal string in the unsigned 64-bit range, incremented on revoke/re-arm; exhaustion forces disarm/session renewal |
| heartbeat_seq | Separate increasing unsigned 64-bit decimal string scoped to boot/session/arm; duplicates cannot renew |
| telemetry_seq | Separate increasing unsigned 64-bit decimal string per boot/session; report gaps; exhaustion requires session renewal |
| device times, extended counts | Times unsigned 64-bit decimal strings; counts signed 64-bit decimal strings with no plus sign, leading zeros or negative zero |
| physical requested values | Finite JSON numbers with explicit field-name units; strict range checks before state change; bool rejected |

HELLO includes a fresh 128-bit host nonce; a HELLO_REPLY must echo it and bind
boot/session. Random identity collision risk is nonzero; an unavailable entropy
source or detected reused identity leaves the endpoint disarmed/faulted. A
reconnect does not transfer the old arm/lease. A duplicate outstanding HELLO
can return its same reply only while that session is disarmed; otherwise require
a new exchange that first revokes arming. These are future engine requirements.

For all non-STOP state-changing requests, require a fresh boot/session, strictly
increasing request_id and t < not_after_us <= t + Wmax. ARM also names the current
configuration ID and exact newly issued one-use arm challenge, valid for Wmax;
consume it on ARM, STOP, fault, session change or expiry. A delayed old ARM cannot
rearm after revocation. CONFIGURE carries the current configuration generation
and increments it atomically; stale generations cannot overwrite new settings.

Keep a bounded eight-entry deduplication cache keyed by session/request ID plus
the exact accepted payload bytes. Before returning a cached result, recheck
current state/session/arm and deadline. Return historical outcome plus current
state without re-executing; never present a historical ACK as current authorization.
Conflicting bytes at the same ID cause an ID_CONFLICT fault. IDs older than the
highest accepted ID, once evicted, return STALE_ID. ID exhaustion requires a new
disarmed session. STOP never uses this replay-suppression rule.

## Framing decisions and first implementation

Retain QWP1 framing and 1024-byte payload/1050-byte total bounds from revision
0.1. No raw CR anywhere; exactly one raw terminal LF per frame. JSON string CR/LF
must be escaped. Byte length is UTF-8 payload length, not code points. CRC is
uppercase eight-digit hexadecimal over exact magic, canonical length, separators
and payload bytes, excluding the final space/CRC/LF. Whitespace variations
inside otherwise valid JSON alter CRC and duplicate-request identity.

Complete codec vectors (LF is shown escaped here):

```text
QWP1 2 {} 71E17289\n
QWP1 34 {"mode":"SIMULATED","type":"STOP"} FCCAB49A\n
```

These are framing fixtures, not valid controller requests: they intentionally
omit the future envelope fields. The codec is not a command-schema validator.
The Python implementation rejects non-object roots, duplicate keys, invalid
Unicode/nonfinite numbers, JSON integers outside signed 64-bit range, more than
four nesting levels or 128 tree nodes including object keys. Exact long
counters/timestamps use strings. Encode-side validation is bounded before JSON
serialization; decode-side parsing sees at most 1024 payload bytes.

The caller-polled parser processes at most 256 supplied bytes per call, stores
at most 1050 line bytes, and returns ordered message/error events without a
retained event queue. Its explicit timeout parameter is supplied by offline
fixtures, not a hardware constant. The future dispatcher must process fault
events before later normal messages; decoding alone never enables hardware.

On an error, discard through the next LF and restart only after that boundary;
never scan rejected payloads for embedded magic or STOP. A STOP appended to an
incomplete/rejected line may be discarded. A STOP sender must provide a clean
line boundary; damaged/blocked links rely on the device lease plus independent
stop, not a promise that serial STOP bypasses bytes already in flight. An expired
partial frame emits one error, clears retained bytes and discards until LF.
The future armed dispatcher latches the parser error even if a later STOP is
decoded, so STOP cannot clear that fault.

UART bandwidth is unresolved. For a proposed b bits/character and baud B,
frame transmission time is bytes*b/B; compare total worst-case request, ACK and
telemetry traffic plus a validated margin against B. Include an already-started
maximum TX frame, UART/USB buffering, Pi jitter and parser work in latency tests.
Reject a configuration that cannot meet its lease/command/telemetry deadlines.
Alternative smaller framing/telemetry limits remain review options; historical
115200 is neither selected nor verified.

## Physical angle, commissioning and optical timing

Telemetry must distinguish electrical encoder counts, inferred gearbox/QWP
mechanical angle, and independently calibrated optical fast-axis angle. The
ratio equation alone does not measure fast-axis angle. Record mechanical zero,
optical zero offset, sign, reference method, calibration ID/date and uncertainty.
Backlash, compliance, eccentricity, gear/holder slip and reversal can invalidate
the ideal ratio model; a motor-side encoder cannot reliably detect downstream
slip. Do not label motor-count feedback as direct optical-angle measurement.

Review full-revolution calibration against a physical QWP reference in both
directions and repeated runs, measuring hysteresis/repeatability. Consider
one-direction acquisition/backlash take-up or an independent output reference
as design options, not installed capabilities. Need for an output-side index or
optical reference remains open. Reset/count discontinuity invalidates reference;
slip invalidation is limited by actual available observability.

Normal ARM requires valid calibration/reference. Initial reference may be
established with motor power independently isolated using a separately approved
manual procedure; no manual movement is authorized here. Powered homing or
commissioning would need its own reviewed state machine and bounded permit.
It is absent from normal motion commands and cannot bypass ARM by default.

A future detector record needs exposure start/end and midpoint, detector-clock
identity and a bounded mapping to Pi/device monotonic time. Interpolate between
fresh encoder samples that bracket the exposure, retaining raw times/counts and
uncertainties. Missing brackets, reference loss, unsolved clock drift or reversal
across backlash must invalidate/flag association, not use commanded angle.

Budget angular uncertainty from count quantization, reference/calibration error,
clock uncertainty times measured speed and mechanical error. For conservative
bounds add the absolute contributions; use root-sum-square only with justified
independence/distribution assumptions. Long integration causes angular averaging,
not merely midpoint timestamp error. Validate the budget against required Stokes
and DoP accuracy and the model's 2θ/4θ harmonics. Required tolerance and detector
rate remain unset; no calibrated reconstruction is implemented by this design.

Velocity estimation must document its sample window, group delay and low-speed
quantization. Tune loop rates/gains/filters from measured plant and encoder data,
checking speed ripple/jitter, settling, steady-state error, angular repeatability
and optical error. Saturation freezes/clamps or back-calculates integrator by one
specified tested rule; reset on disarm/fault/mode changes. Reversal must ramp to
the approved near-zero state and reset/freeze accumulated bias before changing
direction; derivative, if used, is filtered measurement derivative. Stall remains
ambiguous with missing/broken encoder feedback; no uninstalled current sensor
or coupling-slip detector is assumed.

Keep STBY-high and independent energy-cut limitations from revision 0.1. Obtain
motor rating/current specifications or separately approved bounded low-energy
characterization and compare worst-case current with driver/supply capability
before any intentional loading. Intentional powered stall is not an early
prerequisite or authorized test. Routine stopping, fault inhibition, reset output
state and independent emergency isolation each require separate physical evidence.

## Review disposition and offline acceptance

| Review items | Disposition |
| --- | --- |
| 1–4: state, timing, STOP, IDs, parser | Resolved as normative proposed rules above; numeric budgets, UART settings and engine implementation remain pending |
| 5–6: motor energy and stall testing | Accepted; independent disable and electrical suitability remain physical gates, with loading tests deferred |
| 7–9: physical/optical angle, commissioning, detector timing | Accepted; terminology, invalidation and uncertainty requirements recorded; no new sensor or calibration claimed |
| 10: feedback-controller precision | Accepted; measured metrics and filter/anti-windup/reversal review required; no gains or stable-performance claim |
| 11: tests | First 14 framing/parser tests executed; all controller/firmware/physics tests below remain planned |
| 12: preservation and publication | Existing application work preserved; share design/tests through the issue without merging/pushing application history |

No substantive disagreement with the review. Deliberate tradeoff: a STOP with
stale identity can inhibit, while ordinary commands cannot; this favors stopping
over availability. Another tradeoff is conservative LF-only parser recovery,
which prevents payload promotion but can discard a STOP in a damaged line.

Add offline engine tests for duplicate/stale STOP and ARM; new HELLO while running;
all event-precedence ties; new-ID delayed heartbeat; future expiry; equality at
every timer boundary; ID exhaustion/collision; malformed/partial/oversize line
then STOP; TX saturation; reset/clock discontinuity; motion-duration/travel caps;
reference loss during commissioning; encoder wrap, quantization, modeled backlash
and downstream slip; clock offset/drift and exposure association. Every motor
simulation remains explicitly labeled SIMULATED. The original 51 app tests and
new codec tests are separate from future firmware/bench validation.

Implementation stage 1 files: `polarimeter_protocol.py` and
`tests/test_protocol.py`. Reproduce with
`.venv/bin/python -B -m unittest tests.test_protocol -v`.
No new dependency or compiler/toolchain installation is required. The hardware
backend, acquisition guard and GUI rotation behavior remain unchanged.
