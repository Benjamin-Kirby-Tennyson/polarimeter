# Offline motor simulation and controller stage 3

Status: **SIMULATED REFERENCE SOFTWARE**, not measured motor behavior or
installed firmware. No live ports, GPIO, firmware builds, flashing or physical
commissioning were used. The GUI and physical adapters remain unchanged/locked.

Review: [ChatGPT Stage 2 review](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7209103792).
Published review report and controller source: [Stage 3 response](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7209591676).
Earlier reports: [Stage 2](offline-controller-stage2.md) and
[protocol revision 0.2](proposed-motor-protocol-rev02.md).
Reported hardware remains in the unchanged [canonical context](hardware-integration-context.md).
This report supersedes Stage 2 implementation descriptions where behavior changed.

## Controller review fixes

1. Challenge issuance, ARM and MOVE_TO now check that inferred position is
   inside travel limits. Stationarity requires an explicit Readiness policy with
   a velocity band and sample-time dwell; duplicate timestamps cannot advance it.
2. Input is now immutable EncoderSample(raw count, integer sample time, continuity
   identity, validity), not caller-supplied independent angle/velocity fields.
   SimulatedFeedback is internally derived. Exact duplicate samples are accepted;
   contradictory same-timestamp, backwards-time, changed-continuity and excessive
   count-rate samples fault and invalidate reference. Physical A/B decoding is
   still absent; count-rate plausibility cannot detect every missed pulse/slip.
3. An explicit SimulatedReference carries count origin, QWP zero, sign,
   calibration ID and reference epoch. Staleness/loss/continuity/calibration
   changes revoke reference and challenge. Valid-looking later samples cannot
   restore them automatically. establish_reference is an inhibited, stationary,
   simulation-only operation; it neither moves hardware nor proves that moving
   a coordinate zero restores physical travel limits. New epochs are required;
   arithmetic is checked before changing the reference.
4. Fault clearing uses cause-specific predicates. Travel faults require fresh,
   stationary, referenced in-range evidence; feedback faults require an explicit
   new reference after loss. Protocol/queue faults require clean status exchange
   and restored admission. ID_CONFLICT requires HELLO/session rotation. External
   inhibit must be released. Stalls additionally need an explicit simulated
   release acknowledgment, which is not a physical current/shaft measurement.
   Clock/identity/arithmetic-integrity faults remain non-clearable in that instance.
5. Maximum ordinary/heartbeat ID is a terminal sentinel: it inhibits and requires
   disarmed session renewal, rather than allowing wrap. Stale-arm heartbeats
   cannot renew or execute and are ignored for current-arm payload-conflict
   detection; this chooses availability over faulting on stale-arm byte conflicts.
   Current-arm conflicts still fault. Every controller reply is explicitly a
   FINAL_CYCLE snapshot; outcomes record request disposition, not earlier state.

The standalone harness can perform completion/fault checks after a protocol
controller cycle; its returned plant/controller snapshot is the later harness
state. Neither response type proves real-time scheduling or physical stopping.

## Simulated plant and estimator

[MotorPlant](../polarimeter_simulation.py) models gearbox-output velocity w and
angle x. For held signed duty u and dimensionless load disturbance l:

```text
net = u - l
effective = sign(net) * max(abs(net) - friction_duty, 0)
target_velocity = gain_gearbox_deg_s * effective
w_next = target_velocity + (w - target_velocity) * exp(-dt / tau)
x_next = x + target_velocity*dt + (w - target_velocity)*tau*(1-exp(-dt/tau))
```

This exact zero-order-held first-order integration is a transparent fixture,
not a measured TSINY electrical/mechanical model. Friction is an input dead-zone
approximation; load is an equivalent duty disturbance. There is no current,
voltage, torque, temperature or driver-transient model. A simulated stall freezes
the shaft by explicit test intervention; it does not model an intentional powered
stall test. Timestep and signed-duty bounds are explicit. Numeric/time errors
in the harness inhibit simulated output before propagating an exception.

Truth paths remain separate:

- Gearbox truth generates rounded encoder counts with explicit sign and optional
  bounded seeded count noise. Counts/revolution refer to the gearbox output.
- Ideal QWP angle is gearbox angle / external ratio. The external ratio appears
  once; counts per QWP revolution = gearbox-output counts/rev * external ratio.
  No nominal internal gearbox ratio is multiplied into this fixture again.
- Mechanical QWP truth uses a play/backlash interval around ideal angle.
  Encoder inference cannot observe which edge is engaged after reversal.
- Optical fast-axis truth adds an explicit optical zero offset and optional
  downstream slip. Neither is observable from motor counts alone. No independent
  output/optical sensor is invented or represented as already installed.

The estimator derives inferred angle from count origin/sign/calibration and
velocity from count differences across a configurable window. It retains at
most 128 samples; the actual window can shorten if that capacity is exceeded.
For constant acceleration the window-average velocity lags instantaneous speed
by approximately half the window. Low count rates can conceal motion between
counts. These estimates are not plant truth or direct optical-angle measurement.

## Trajectory and feedback control

A position proportional speed request is limited by commanded speed,
sqrt(2*acceleration*remaining_distance), and slew-rate/acceleration bounds.
An explicit position band requests zero speed near the target. Position gain,
speed, acceleration and all bands are supplied as fixture parameters.

Speed PI uses estimated encoder velocity, finite duty saturation and conditional
integration anti-windup. Integration occurs in range or towards recovery from
saturation; the integral also has an explicit bound. No derivative is used.
PI resets on inhibit, new motion generation and completed reversal dwell.
Reversal ramps the command towards zero, requires estimated near-zero velocity
and command for an explicit dwell, resets PI, then slews in the opposite direction.
This is a model policy, not approval of TB6612FNG braking/reversing behavior.

Completion requires inferred position and velocity inside explicit bands for
a sample-time dwell, then inhibits and revokes ARM. Further movement requires
explicit rearming. STOP/fault output inhibition resets PI but does not instantly
remove the plant's velocity. A stopping-distance predictor uses estimated
velocity, a configured deceleration and margin to inhibit near travel limits;
its assumptions are not a guarantee of mechanical containment. Encoder stall
detection uses fresh feedback, explicit duty/speed/time bands and startup grace.
It cannot distinguish all encoder failures from a jam or provide current limiting.

## Reproducible parameters and quantitative results

All following numbers are **invented SIMULATED fixtures**, not TSINY/TB6612FNG
ratings, measured encoder resolution, safe limits or hardware tuning.

| Fixture parameter | Value |
| --- | --- |
| Plant gain / time constant | 180 gearbox-output deg/s per duty / 0.12 s |
| Gearbox-output counts / external ratio | 36000 decoded counts/rev / 1.5 |
| QWP count scale | 54000 counts/rev, about 0.006667 deg/count |
| Input dead zone / optical truth offset | 0.02 duty / 5 deg |
| Controller sample / velocity window | 5 ms / 20 ms (half-step comparison: 2.5 ms) |
| Position gain / requested speed / acceleration | 2 per second / 30 deg/s / 60 deg/s² |
| PI Kp / Ki / integral bound / duty bound | 0.012 duty per deg/s / 0.08 duty per deg / 0.7 / 0.8 |
| Settle position / velocity / dwell | 0.08 deg / 0.2 deg/s / 200 ms |
| Stationary readiness band / dwell | 0.1 deg/s / 10 ms |
| Reversal dwell / prediction deceleration / margin | 50 ms / 200 deg/s² / 1 deg |
| Stall duty / velocity / duration / startup allowance | 0.7 / 0.1 deg/s / 500 ms / 500 ms |
| Lease / request / feedback age / max cycle gap | 200 ms / 200 ms / 50 ms / 20 ms |
| Motion duration | 12 s request, within the 20 s configured maximum |

Nominal move is 0→90 deg; plateau statistics cover seconds 1–2 after the request.
Completion times include the settling dwell. Signed errors are estimate/truth
minus requested target. No additional optical zero calibration is applied.

| Scenario | Completion | Estimated final error | Mechanical truth error | Plateau mean / peak-to-peak ripple |
| --- | --- | --- | --- | --- |
| Nominal, 5 ms | 5.240 s | -0.04667 deg | -0.04550 deg | 29.95854 / 0.66667 deg/s |
| Nominal, 2.5 ms | 5.2475 s | -0.04667 deg | -0.04591 deg | 29.96093 / 0.66667 deg/s |
| 1 deg backlash | 5.240 s | -0.04667 deg | -0.54550 deg | 29.95854 / 0.66667 deg/s |
| ±1 count noise, enabled at 1 s | No completion; duration fault at 12 s | +0.04667 deg | +0.03683 deg | 29.96020 / 2.00000 deg/s |
| 0→-90 deg | 5.240 s | +0.04667 deg | +0.04550 deg | -29.95854 / 0.66667 deg/s |

Nominal reported overshoot is zero under this position approach. The earlier
square-root-only position request overshot about 2.83 deg and failed to settle;
adding the bounded position-proportional request resolved that fixture's hunting.
This is a simulation design change, not evidence of physical tuning.

The 20 ms velocity window plus count quantization/noise can exceed the 0.2 deg/s
settling band even when position looks acceptable. The noisy scenario correctly
does not claim completion. Reversing after a +30 deg move with 1 deg backlash
changes estimate-minus-mechanical-truth from approximately +0.5 to -0.5 deg.
An injected 10 deg downstream slip changes optical truth without changing counts.
At STOP, a 29.7963 deg/s simulated QWP velocity coasts approximately 3.5747 deg
over the next second despite zero duty. These limits remain visible in the model.

Run the full parameter and scenario report without creating data files:

```bash
.venv/bin/python -B -m tests.simulation_fixtures
.venv/bin/python -B -m unittest tests.test_protocol tests.test_controller tests.test_simulation -q
.venv/bin/python -B -m unittest discover -q
```

Verification: **126 discovered, 119 passed, seven opt-in GUI tests skipped**,
zero failures/errors. The 75 offline protocol/controller/plant checks comprise
14 codec, 41 controller and 20 simulation tests. Earlier GUI passes remain
historical; no GUI rerun or actual firmware build is claimed in this stage.
Tests include analytic plant integration, timestep convergence, saturation,
reversal dwell, quantization/noise, backlash/slip, load, stalls, reset/feedback/link
loss, bounds, cause-specific clearance, reference lifetime and session exhaustion.

## Remaining limits

The simplified plant, gains, encoder scale, ratios, noise and timing are not
identified from the actual system. Optical integration windows, clock mapping,
Stokes accuracy, plant current/thermal safety and independent energy interruption
are not validated. Sample/JSON work is bounded by fixture capacities but has no
ESP32 execution-time proof. Host/GUI integration and actual replacement firmware
remain future stages; the installed firmware still has no verified protocol.
No power, wiring, flashing, port-open or motion approval follows from these tests.
