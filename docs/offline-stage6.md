# Stage 6: raw estimator and ordered offline endpoint

2026-11-27. **SIMULATED/COMPILE-ONLY SOFTWARE; PHYSICAL CONTROL LOCKED.**
Responds to [Stage 5 re-review](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7229422978).
The priority is a reliable target estimator/admission/endpoint boundary, followed
by independent review. Portable trajectory/PI and optional GUI Analyze polish
are deferred until this integration is reviewed; the existing explicit CLI
analysis workflow remains available. No actual UART/PCNT/LEDC/driver is added.

## Exact admission and ordered events

Schema admission now uses exact decoded key membership and exact root field
count. Delimiter-containing and escaped keys cannot match adjacent allowed
names. Golden vectors include `v|mode`, `mode|type`, other delimiter forms and
escaped equivalents. Failed frame/schema admission resets reused Command output;
a partial candidate is never exposed. Source annotations now distinguish the
previous successful build from unperformed target execution.

The parser retains up to eight **ordered frame/error events** alongside its
1050-byte partial buffer. Multiple frames in one 256-byte chunk cannot overwrite
an earlier STOP or error. Endpoint processing consumes a whole batch before
controller admission. Queue overflow faults/inhibits before any command mutation,
even if STOP cannot fit. Fatal timing errors (zero timeout/backwards clock) latch
and cannot recover merely by LF. Ordinary partial-frame expiry emits an error,
discards through LF and preserves ordering on subsequent valid traffic.
Counters saturate on uint64 exhaustion rather than wrapping. No physical
transport-opening method exists.

`MemoryEndpoint` ties parser events to controller preflight and raw sample input;
error/STOP combinations and over-capacity traffic have explicit integrated native
checks. A separate [transport/clock contract](offline-transport-clock-contract.md)
defines the future byte/time/mapping boundary without inventing a baud or real
clock characterization.

## Internally derived encoder and reference

The old caller-supplied Gates booleans are removed. Each coherent mock sample
contains a uint64 microsecond timestamp, signed-int64 count, validity/count-present
flags and continuity identity. The estimator derives count difference, configured
scale/sign, velocity window and stationary dwell internally. It checks future,
backwards and contradictory same-time samples, continuity reset, count-rate
limits, sample age, gap and finite angle/velocity. Signed subtraction is checked;
there is no assumed hardware rollover/CPR/encoder supply or pinout.

Reference establishment is an explicit simulated API using a new epoch,
calibration identity, raw count zero, mechanical angle zero and sign. It requires
inhibited logical motion, configured scale and fresh stationary count evidence.
Reused epochs are rejected within the bounded history. Calibration change,
invalid/stale/gapped/reset/contradictory feedback invalidates the reference and
challenge; ordinary healthy data cannot resurrect it. Travel faults preserve the
original mechanical frame and reject re-zeroing to hide an out-of-range condition.
ARM/challenge/motion require internally fresh, referenced, inside evidence.

One deliberate hardening affects **both Python and C++**: a new sample arriving
after a gap reaching the configured feedback-age limit causes FEEDBACK and
invalidates reference. Previously a newly fresh sample could hide a missed
interval. Recovery requires coherent samples, a new explicit reference epoch,
cause-specific fault clear and a new explicit arm sequence. Challenge-expiry
regression now keeps samples flowing so it tests challenge expiry independently.

Recovery predicates now cover fresh referenced feedback, stationary/inside
status, external inhibit release, healthy control cycle, protocol clean/admission
recovery, session taint/exhaustion and cleared timers. Permanent faults remain
unclearable. Physical stopping, current/thermal behavior and encoder electrical
safety are outside this model.

## Priority, scope and oracle tests

Both sides of preflight conflict comparison filter STOP/HELLO, wrong session and
stale-arm heartbeat traffic. Duplicate disarm outcomes are retained per event,
not request ID, so repeated identical events can report ACCEPTED then
RETRY:ACCEPTED. Multiple HELLO and exhausted identity factories follow the Python
fault/generation semantics. Faults precede terminal completion.

Shared traces feed actual framed bytes/errors through the bounded endpoint and
bootstrap both implementations by HELLO, raw count samples,
CONFIGURE and explicit reference establishment. They do not assign ready/inside
booleans, configured/armed state, epochs or generations externally. Snapshot
comparison includes angle/velocity, freshness/inside/stationary, reference and
challenge lifetime, ordered ACKs, state/config/arm generations, session,
lease/motion deadlines, faults and retained terminal boot/session/arm/motion/time.
Angle/velocity comparisons use a documented numerical tolerance of
`1e-7 * max(1, abs(expected))`; identity/state/outcome fields compare exactly.

There are **39 traces / 491 compared actions**. Added cases include stale/current
heartbeat permutations, STOP with same ordinary ID in both orders, duplicate
disarm, multiple HELLO, request/identity exhaustion, invalid/stale/gapped feedback,
no reference resurrection, reference epoch reuse/reestablishment, continuity
reset, count-rate and signed-subtraction overflow, negative sign/windowed speed,
calibration change, external release, travel return/recovery and clean-protocol/
admission recovery. The `complete_fixture_only` API is an explicitly requested
oracle hook, not encoder-measured settling/completion and not a wire command.
Target measured completion/trajectory/PI remains a later increment.

## Analysis integrity follow-up

Every scalar/list/matrix result is checked for finiteness, including condition,
DoP, propagated standard errors and coverage. With a declared detector period,
a midpoint interval above 1.5 periods is flagged as a temporal gap even if IDs
are consecutive, and cannot add contiguous coverage. This is a stated analysis
quality policy for the simulated cadence, not a measured instrument bound.
If cadence is absent it is flagged CADENCE_UNSPECIFIED instead of certifying
continuity. Sidecar analysis uses the acquisition's recorded period.

New finalized manifests record full optical acquisition identities. Analysis
checks all recorded experiment/boot/session/arm/motion/reference/config/encoder-
calibration identities, rows/valid/flagged/received counts and detector accounting
where present, plus existing CSV/manifest hashes. Optical fit calibration remains
separate. Mutated identity/count manifests and nonfinite scalar outputs have
regressions. Conditional IID intensity-noise uncertainty still excludes actual
clock/angle/retardance/optical calibration errors. CLI sidecars are retained;
optional read-only GUI results are deferred to avoid delaying target work.

## Verification and resource evidence

Final Pi desktop run: **189 tests passed**, zero failures/errors/skips, including
**eleven mapped GUI tests**, in 30.837 s. The complete CLI scan → selected
completed CSV → hashed sidecar workflow was rerun with the stricter identity/
count checks. After the final malformed-manifest value guards, eleven related
analysis/cadence/controller-gap tests were rerun and passed. The final framed-
endpoint native/ESP32 verification is recorded below.
No compiler, package installer, physical port or peripheral ran on the Pi.

Final [hosted run 46080613651](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/actions/runs/46080613651)
compiled source `1813dc84f87fc64e6c39c402f13ffd3538b817fc` and both jobs passed:

| Layer | Actual result |
| --- | --- |
| Native framed-endpoint conformance | **883 passed, zero failed**, GCC 13.3.0, C++17, address/undefined-behavior sanitizers, warnings as errors |
| Classic ESP32 compile/link | **PASSED**, ESP-IDF v5.3.4, SDK `1b459d9c4950395dec12ce19256c73f9a41e306f`, Xtensa GCC 13.2.0 (`esp-13.2.0_20240530`) |
| Target execution / physical validation | **NOT PERFORMED** |

[Persisted Stage 6 evidence](build-evidence-stage6-20261127.json) contains versions,
artifact/component SHA256, resource report and compiler `.su` stack estimates.
The artifact ZIP hash was independently checked on download. Seven-day CI artifacts
include logs, config, ELF/bin/map and stack files. Target image report: **503200
bytes**, static IRAM **51538 bytes**, DRAM **11080 bytes**. Binary SHA256:
`f2f29aed518674ef940e863931c20fb7e88202b6bb9f15ab32b8dab67fbb443b`.

Selected target compiler stack estimates (bytes per function, not high-water):
`admit_candidate` 6960; controller cycle 960; endpoint cycle 96; generated trace
runner 14848; selftest 5856; app_main 80. Nested calls, library/interrupt use and
runtime heap are not captured by summing isolated map sections. These figures
require review before executing the image; successful link is not proof of a
safe 32768-byte runtime stack margin. Native x86_64 object sizes were Json 8232,
Controller 1408, MemoryEndpoint 5984 and RawSample 56 bytes; they are not ESP32
object-size measurements. No target scheduling or physical watchdog claim follows.


The first hosted raw-input run found one identity-exhaustion generation mismatch
(863 checks passed, one failed). The outer HELLO fault transition was aligned to
the Python reference. A later GUI test cleanup race was fixed by awaiting its
asynchronous worker/overlay shutdown before deleting temporary files; the test
also permits a legitimate new-session snapshot while rejecting stale identity.
Neither change relaxes the hardware lock or masks lost status.

Bounded data structures still use C++ strings, vectors, deques and a fault set;
this is **not a constant-allocation or real-time scheduling claim**. Bounds are
1024-byte payload/1050-byte frame, depth 4/128 JSON nodes, eight endpoint events/
commands/cache records, 128 raw window samples and 32 recent identity/reference
epochs. All protocol fault strings come from a finite internal vocabulary.
Duplicate-key comparisons can be quadratic within the node bound. Heap allocator
latency/fragmentation, OOM/reset behavior, worst-case control execution and actual
stack high-water remain unmeasured. Native object sizes and compiler `.su` stack
estimates are collected separately; static map sizes do not measure runtime heap
or nested-call/interrupt stack requirements. The configured 32768-byte main
mock-test stack is not a demonstrated hardware margin.

## Next physical evidence and remaining software work

The [commissioning checklist](physical-commissioning-checklist.md) separates user
observations with all power disconnected from later authorized logic/encoder and
powered motor/optical tests. Highest-priority evidence: actual board/module/flash
labels, original Windows Arduino sketch folder/build/upload provenance, documented
encoder mapping/supply/logic, actual driver nets/STBY/ratings, motor current data
and independent default-inhibited energy isolation. Actual gear teeth/zero and
detector model/interface are also unresolved. No arbitrary physical values are
provided. Record UNKNOWN until evidence is available.

Next software increment after independent estimator/endpoint review: portable
trajectory/PI with explicit fixture parameters, numeric/anti-windup/reversal/
settling/fault tests against the Python model; fixed-allocation/operation-budget
review; then a separately reviewed physical transport/PCNT/driver plan. SDK/CI
pins and board configuration require deployment review. This work authorizes no
physical access, flashing, actuation or merge.
