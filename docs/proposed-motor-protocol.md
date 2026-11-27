# Proposed ESP32 motor firmware and Pi protocol

Historical revision: [revision 0.2](proposed-motor-protocol-rev02.md) now provides
the normative state, timing, identity, parser and physical-angle clarifications.
Its rules supersede conflicting wording in this revision 0.1 design.

Status: **PROPOSED, DOCUMENTATION ONLY, NOT IMPLEMENTED**. Revision 0.1,
2026-11-26. This describes a possible replacement design for review, not the
installed firmware, its command language or permission to operate hardware.
No application behavior or hardware configuration changes accompany this file.

Requested review scope: [ChatGPT / CAD_QWP reply](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7208732419).
Published review copy: [GitHub specification comment](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7208782261).
Reported hardware belongs in the [canonical hardware context](hardware-integration-context.md).
The [integration audit](codex-integration-audit.md) records actual source findings.
Every protocol name, capacity and policy below is a design proposal; numerical
operating parameters remain unset until measured and approved.

## Responsibilities and boundary

| Component | Proposed responsibility |
| --- | --- |
| ESP32 | Fixed-period encoder sampling, extended counts, trajectory limits, speed feedback loop, PWM/direction, fault handling and communication watchdog; communication work must not block the control deadline |
| Raspberry Pi | Explicit connection/arming UI, high-level target or velocity requests, bounded worker queues, telemetry validation, logging and plotting; no PWM timing or safety dependence on Tk callbacks |
| Optical acquisition | Separate device identification, calibration, integration timing and encoder-time association; not part of this motor protocol revision |
| Independent hardware stop | Remove motor energy without depending on Pi, USB or running firmware; remains a separate physical design and validation requirement |

Proposed signal flow:

```text
Pi UI -> bounded serial worker -> ESP32 command validation -> trajectory limiter
                                                          -> speed controller
Encoder A/B -> verified decoder -> counts/angle/velocity ---^       |
                                                                 v
                                              TB6612FNG -> motor -> QWP
Independent physical energy disconnect --------------------^ power path
```

The reported TB6612FNG Channel A mapping is PWM GPIO18 and direction
GPIO19/GPIO21; see the canonical map for complete connections. This is
DOCUMENTED history, not firmware or physical verification. Encoder pins,
supply and output levels remain UNKNOWN. Deprecated driver pins and suggested
encoder pins must not become defaults. No GPIO is changed by this proposal.

## Driver outputs and hardware disable

The [Toshiba datasheet, pin functions and H-SW control table](https://toshiba.semicon-storage.com/info/TB6612FNG_datasheet_en_20141001.pdf?did=10660&prodName=TB6612FNG)
distinguishes drive, short brake, high-impedance stop and standby. Firmware must
use reviewed truth-table combinations for the verified circuit. Zero PWM alone
must not be described as electrically disconnected or as confirmed mechanical
stopping. Coast/brake selection and direction-change sequencing require review
of the load, supply transients and measured stopping distance.

STBY is currently reported tied high. Thus a host request cannot independently
put the driver into standby, and a software hang can leave drive active. For
review, propose a default-inhibited enable path and independent energy cut,
including reset/power-loss behavior. GPIO-controlled standby alone would still
not be an independent emergency stop. No new pin or circuit topology is selected.
Confirm motor startup/stall current, driver thermal capacity, supply/logic
levels, ground returns, decoupling and unused-channel termination before power.

The future firmware must initialize reviewed inactive output combinations
before allowing PWM, invalidate all arm sessions after boot, and never resume
motion from nonvolatile settings. External hardware must establish the default
inhibit during reset, before code executes; firmware initialization cannot
guarantee that interval. Hardware operation remains blocked until this is reviewed.

## Encoder configuration and measurement

Configuration needs explicit, verified A/B pins, supply limits, input logic,
pullups/translation if required, decoding mode, filter timing and maximum edge
rate. There is no assumed encoder supply, color mapping, CPR or pulses/revolution.
The historical 12, 900 and 3600 values are not calibration inputs.

Use signed extended counts with an explicit physical reference shaft. A
candidate ESP32 implementation may use PCNT; its hardware counter limits,
extension/overflow accounting and glitch filtering must be tested with the
chosen framework version. [Espressif PCNT documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/pcnt.html)
describes quadrature decoding and counter-limit behavior; it does not verify
this project's wiring or encoder rate. Framework/version selection is open.

Let Cg be experimentally validated decoded counts per gearbox-output revolution,
and R be driven-QWP teeth divided by motor-pinion teeth. Then Cq = Cg * R is
counts per QWP revolution. If starting with motor-shaft pulses instead, include
the selected decoding multiplier and measured gearbox ratio once to obtain Cg.
Do not apply the gearbox ratio again to an output-shaft calibration. Neither
the nominal gearbox ratio nor the proposed gear tooth counts is certified here.

For a calibrated count origin c0 and validated direction s in {-1,+1}:

```text
qwp_angle_unwrapped_deg = s * (count - c0) * 360 / Cq
qwp_angle_wrapped_deg   = qwp_angle_unwrapped_deg modulo 360
qwp_velocity_deg_s     = s * delta_count * 360 / (Cq * delta_time_s)
```

Use actual device monotonic sample times, not serial arrival time. Record
velocity-window/filter delay and uncertainty. Explicit multi-turn targets use
unwrapped angles; wrapped angles are display values, not implicit shortest-path
commands. Invalidate absolute angle after reset, uncertain count continuity,
coupling slip or loss of zero reference; require re-reference. Never silently
zero while armed. Incremental counts alone do not establish absolute QWP zero.

## Proposed bounded UART framing

Baud, data bits, parity, stop bits and flow control must be a reviewed explicit
transport configuration. No baud value is chosen here, and the host must not
probe the installed board or send a handshake to discover it. Any future open
requires separate authorization and reset-safety review.

Candidate frame grammar (illustrative text, not a command to send):

```text
QWP1 <decimal_payload_byte_length> <UTF-8 JSON payload> <8-hex CRC32> LF
```

Propose payload sizes 1..1024 bytes, total frame limit 1050 bytes, integer
length with no leading zeros, and CRC-32/ISO-HDLC over the exact bytes from
QWP1 through the payload, including its length and separating spaces. CRC
parameters: polynomial 0x04C11DB7, reflected input/output, initial and final XOR
0xFFFFFFFF; interoperability must include the check vector 123456789 -> CBF43926.
CRC detects accidental corruption; it provides no authentication.

The receiver reads the stated payload length, then validates trailer, CRC and
strict JSON object schema. Reject duplicate JSON keys, excessive nesting (proposal:
four levels), unknown fields, nonfinite numbers, out-of-range integers and invalid
UTF-8. Payload newlines must be JSON-escaped. Reject oversized length before
allocation. Bound incomplete-frame age and resynchronization work; malformed
traffic cannot starve the control loop or refresh a motion lease. Parser failure
while armed inhibits new motion and latches a communications fault. Byte-rate,
frame-time and parser-deadline budgets must be set from the chosen UART rate.

Propose fixed upper bounds of eight pending requests, eight control responses
and 32 telemetry records, with only one in-flight motion request. STOP handling
must bypass ordinary motion queues after a complete valid STOP frame is parsed.
It cannot bypass UART serialization, incomplete input or a broken link. TX
backpressure must not delay output inhibition; control-response overflow faults
closed. Telemetry may coalesce to the newest snapshot with a reported dropped
sample counter; acquisition must reject unaccounted gaps. No unbounded queues.

## Version, identity and message lifecycle

Common envelope fields: protocol version v, explicit mode (SIMULATED or HARDWARE),
message type, boot_id, negotiated session_id and monotonically increasing
message id. Boot identity and new session binding prevent stale-request reuse;
they are not cryptographic authentication. Define fixed field/string bounds in
the eventual schema. Unsupported major versions keep the host disconnected from
motion; optional features require advertised capabilities and schema agreement.

| Proposed message | Purpose and validation |
| --- | --- |
| HELLO / HELLO_REPLY | No motion; negotiate version, host nonce echo, boot/session identities, firmware build identity, device capabilities, device monotonic time and applied configuration/calibration identifiers |
| GET_STATUS / STATUS | State, faults, references and measured telemetry; never arm |
| CONFIGURE / ACK | Only while disarmed and stationary; atomically validate approved complete configuration, report its ID, reject missing/unknown values; no arming side effect |
| ARM / ACK | Explicit user request for the current boot/session/configuration; requires validated reference, fresh feedback, no fault, approved physical readiness and inactive trajectory |
| HEARTBEAT / ACK | Advance a session-scoped monotonic heartbeat sequence with a device-time expiry; only fresh valid heartbeats renew the armed lease |
| MOVE_TO / ACK | Unwrapped QWP angle in degrees, bounded speed and acceleration; all numbers finite and within device-enforced configured limits; reject while another trajectory is active |
| SET_VELOCITY / ACK | Signed QWP degrees/s with configured acceleration and bounded travel; requires a valid calibration and motion lease; no raw PWM host command |
| STOP / ACK / STOP_STATUS | Idempotent inhibition request in any state; clear pending motion and revoke arming before responding; a syntactically valid STOP does not require an arm token |
| DISARM / ACK | Inhibit output and revoke the arm session; no automatic restart |
| CLEAR_FAULT / ACK | Require fault cause absent and reference/feedback revalidated as appropriate; returns disarmed, never running |

Motion requests include the current arm generation, a device-time not_after_us
deadline and unique request ID. Require a fresh GET_STATUS time reference and a
bounded future-deadline window; reject expired or excessively future requests.
Watchdog/STOP/fault revokes the arm generation immediately. A retry of the same
ID and payload returns its cached outcome without repeating motion; conflicting
payloads are errors. Use a bounded deduplication window and reject older IDs
after eviction. Reset requires new handshake/configuration/explicit arming.
Never replay outstanding motion after a host timeout or reconnect.

ACK means accepted/rejected, not achieved position. Errors include stable codes
for bad frame/version/schema, wrong session/state, stale request, missing
reference/calibration, limit violation, busy controller and latched fault.
Motion completion is a separate event only after fresh measured angle/velocity
remain within approved bands for the measured settling interval. Report STOP
output inhibition separately from stationary-feedback detection; neither proves
an independently verified physical energy cut.

## Telemetry and timing

Transmit boot/session ID, sequence, device sample time in monotonic microseconds,
raw signed count, encoder-valid/reference-valid flags, calibration ID, measured
unwrapped/wrapped angle and velocity, requested target/velocity, controller
state, arm generation, applied signed duty, limit/saturation flags, queue/drop
counters and latched faults. Unknown measurements are null with validity flags,
never zero substitutes. Bound strings, arrays and integer ranges in the schema.
Represent long counters/timestamps as decimal strings or another explicitly
agreed exact-integer representation across implementations; test precision limits.

The host stores device time and its own monotonic receipt time separately, with
a UTC session anchor. A new boot invalidates prior device-time mappings.
USB arrival time is not detector exposure time. Actual optical integration
windows, clock mapping/uncertainty and angle association need separate review;
do not reuse simulation CSV for hardware samples. Telemetry timing/rate must fit
the UART bandwidth including framing and worst-case control-response traffic.

## State machine and stop policy

| State | Output/entry policy | Allowed transition |
| --- | --- | --- |
| BOOT / DISARMED | Reviewed inactive outputs, no valid motion lease, no retained trajectory; inspect reset cause | CONFIGURED after complete validated configuration; any detected fault -> FAULT |
| CONFIGURED | Disarmed; configuration accepted, feedback/reference readiness still required | ARMED only on explicit ARM and all gates satisfied |
| ARMED | Output inactive, finite motion lease active | RUNNING on a validated new motion request; STOP/DISARM/lease expiry revokes arming |
| RUNNING | Local bounded control loop; continuously enforce feedback, lease and motion limits | STOPPED after requested stop/completion and output inhibition; any fault -> FAULT |
| STOPPED | No active trajectory or arm token; mechanical stationarity is a separate status | Explicit ARM after renewed readiness; CONFIGURED/DISARMED for reconfiguration |
| FAULT | Trajectory canceled, arming revoked, reviewed fault-inhibit output requested, cause latched | CLEAR_FAULT only with causes removed -> DISARMED; never directly RUNNING |

STOP supersedes pending/active moves and cancels their completion expectations.
All state transitions and timers must be deterministic and bounded. Normal
trajectory deceleration and fault inhibition may require different reviewed
output policies; a fault must not wait indefinitely for controlled deceleration.
Choose braking/coasting and bounded stop deadlines from physical evidence before
implementation. Fresh encoder feedback below a validated speed threshold for a
validated interval can report stationary; missing feedback must report unknown.

A valid heartbeat lease is checked on the ESP32 independently of the Pi UI.
Serial silence, expired/invalid feedback, reset, count discontinuity, missed
control deadlines, overload indicators and lost communication cause inhibition
and latched fault. Require a fresh handshake after reset/disconnect and explicit
re-arm after every stop, timeout or fault; never restart upon resumed traffic.
An MCU hardware watchdog is a separate last-resort mechanism; its reset does
not guarantee inactive driver outputs while STBY is tied high. Exact watchdog,
heartbeat, feedback-age and stopping limits remain unset pending measurements.

## Proposed feedback controller

Use a bounded position trajectory that produces a speed setpoint, with a local
speed PI/PID loop driving signed duty. Position/velocity error uses measured
encoder data and actual elapsed time. Clamp velocity, acceleration, travel and
duty in device firmware; Pi-side checks alone are insufficient. Direction
reversal follows a reviewed zero-speed/output transition policy.

Apply actuator saturation before output. Prevent integrator windup using a
specified clamping or back-calculation rule; reset/freeze the integrator on
disarm, fault and mode change with a defined restart policy. If derivative
action is used, take it from measured velocity rather than setpoint steps,
filter it with a documented bandwidth and account for filter latency. Gains,
loop rate, filters, limits and settling bands are UNKNOWN, with no claim of
stability, tracking accuracy or safe speed. No gain values are supplied.

Stall monitoring compares persistent commanded drive/expected motion against
fresh encoder movement over approved intervals, with explicit startup allowance.
Distinguish stall, stale/broken encoder, position limit and controller saturation.
Encoder-based stall detection does not provide current limiting or prove the
shaft stopped. No motor-current sensor is established, and no current reading
or driver fault pin is assumed. Response thresholds need measured bench evidence.

## Offline verification matrix

These are planned tests, not tests executed by adding this document.

| Area | Required offline checks / acceptance |
| --- | --- |
| Framing | Split/coalesced frames; exact and excessive bounds; bad length, CRC and UTF-8; repeated magic bytes; truncated trailer; oversize/nested/duplicate-key JSON; bounded memory/work and no output-enable side effect |
| Sessions | Wrong version/boot/session/arm generation; duplicate and conflicting IDs; eviction and monotonic-ID rejection; stale/future deadlines; delayed/replayed heartbeat must not extend lease |
| State / stop | Reject all premature motion; STOP before/after queued move and during faults; repeated STOP; exact timeout boundary; fault clearing cannot arm; no resume after reconnect/reset |
| Encoder | Both directions and illegal transitions; hardware-counter wrap/extension; count discontinuity; stale feedback; exact integer limits; zero/reference invalidation and velocity-filter latency |
| Calibration | Counts per reference shaft, x1/x2/x4 conventions and external ratio applied once; positive direction and 0/360/multi-turn behavior; unknown calibration yields invalid/null angle |
| Controller | Setpoint steps/ramps/reversals, variable sample intervals, saturation/anti-windup, stall vs missing feedback, missed deadline, stopping-policy timeout; no claim that model stability validates real hardware |
| Backpressure | Bounded RX/TX/request queues; telemetry coalescing reports gaps; STOP not delayed by motion queue; response overflow inhibits motion; watchdog unaffected by noisy input |
| Pi / logging | Separate requested/measured position, ACK/completion/STOP statuses, stale telemetry and device reset; simulation labels in UI/CSV; separate hardware schema; disk error disables scheduling and requests stop without claiming physical success |

## Staged validation and decisions

1. Review this specification and proposed schema offline. Recover the original
   sketch from the original upload computer if possible, retaining exact source,
   framework/build versions and upload provenance. If unavailable, continue to
   label installed firmware UNKNOWN. Do not silently replace it with this design.
2. Record power-off pin-to-pin wiring, encoder electrical specification, actual
   gearing, startup/stall current requirements, driver/supply/thermal suitability,
   reset behavior and independent energy-disconnect design. Resolve conflicts
   against physical labels and source; do not prescribe breadboard row numbers.
3. Only after explicit approval for the specific activity and verified motor
   power isolation, validate reset/inhibit behavior and any permitted communication
   without motor energy. A port open or firmware upload is a separate action
   requiring approval; it is not implied by a bench-plan review. Run parser and
   simulated encoder tests offline before this stage.
4. Under separately approved supervised bench conditions, verify encoder logic,
   counts/direction/zero and disable behavior before powered motion. Independent
   stop and controlled supply limits must be available. No test is authorized
   by this document, including manual movement or flashing.
5. Only then approve bounded initial motion and measure response, stall current,
   stopping behavior, encoder limits, controller gains, lost-link/reset response,
   speed/acceleration limits and thermal/transient behavior. Verify each fault
   path before optical acquisition or unattended operation.
6. Validate time-correlated encoder/optical acquisition and calibration separately.
   Do not describe Stokes reconstruction or calibrated power as implemented.

Open decisions: original-source recovery and installed build identity; framework
version; reviewed pin map and encoder levels; decoded counts and gear ratios;
independent disable circuit; electrical suitability; UART configuration and
framing/schema approval; control/telemetry rates and bandwidth; finite timeouts;
limits, calibration, filtering and gains; brake/coast policy; stop/stall validation;
detector identity and timing. No hardware readiness gate is satisfied merely by
writing or approving a protocol document.
