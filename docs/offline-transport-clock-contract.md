# Proposed transport and clock boundary

2026-11-27. Offline contract only. `MemoryEndpoint` has feed/cycle methods and
**no port-opening, UART, PCNT, LEDC or driver backend**. Installed firmware and
baud remain UNKNOWN. This contract does not authorize implementing a live adapter.

- Receive at most 256 bytes per feed; retain at most 1050 partial-frame bytes and
  eight ordered frame/error events. Drain a whole bounded batch to controller
  preflight before any logical enable. Overflow faults/inhibits even if STOP
  cannot fit. No frame/error may be replaced silently by a later command.
- Caller supplies monotonic integer microseconds and an explicit positive partial
  timeout. Zero/invalid timing or backwards time latches parser failure; LF cannot
  revive it. Ordinary partial-frame expiry records an error and discards to LF;
  recovery still requires controller protocol/reference/arm gates.
- Every cycle runs even without RX, with an optional coherent mock encoder sample
  (time, signed count, validity and continuity identity). Snapshot/counter output
  is distinct from physical observations. Terminal boot/session/arm/motion/time
  records are retained for session-safe recovery. STOP precedence is batch-wide.
- Real host/device clocks require a separately reviewed mapping object containing
  boot identity, offset interval, drift/rate bound, measurement interval, expiry
  and uncertainty. Establish it using timestamped exchanges with measured delay
  bounds; simulation's known/shared clock is not evidence for a real mapping.
- Convert deadlines conservatively in the device clock domain, subtracting
  uncertainty/transport budget; reject ambiguous, expired, overflowed or overly
  future deadlines. Reset/boot/clock discontinuity invalidates mapping and current
  reference/session/arm context. Never use fresh-looking status to renew an
  outcome deadline or silently resurrect motion/reference.
- Optical association requires a separately characterized detector clock mapping,
  exposure extent and full uncertainty-expanded encoder brackets. Unknown or
  expired mapping preserves raw data but invalidates inferred association.

The native endpoint demonstrates ordering/preflight with virtual time only.
No real serial throughput, independent hardware watchdog, scheduler deadline,
clock drift, reset response or physical stopping guarantee is established.
Measured scheduling, fixed-allocation refinements and real clock characterization
remain later commissioning work after the [physical evidence gates](physical-commissioning-checklist.md).
