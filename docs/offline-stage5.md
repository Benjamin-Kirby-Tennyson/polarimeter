# Stage 5: detector cadence, analysis workflow and compiled mock target

2026-11-27. **SIMULATED/COMPILE-ONLY SOFTWARE.** Implements the
[058225c re-review](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/issues/1#issuecomment-7228930249).
No installed firmware identified, physical port opened, GPIO/PCNT/PWM allocated,
firmware flashed, motor operated or main merged. The existing hardware adapters
and canonical reported wiring remain unchanged.

## Independent detector schedule

Production `RotationSession` now drives three explicit operations independently:
advance simulated physics/detector cadence; observe delivered encoder status;
deliver/associate scheduled optical exposures. Detector physics uses the bounded
truth timeline, never mapped timestamps or received status. Its sequence advances
at fixed 20 ms slots from virtual time zero, including slots outside collection.
Exposure duration is 10 ms; delivery is delayed 20 ms to obtain encoder brackets.
A maximum 32-record delivery queue fails explicitly on overflow. Polling must hit
exposure-end slots; missed polling/clock reversal fails rather than invent timing.

The **collection gate** is defined by the simulated endpoint at exposure END:
controller SCAN/RUNNING and mechanical QWP truth inside the directed planned
plateau. Mechanical truth is used only for this explicit simulated gate and
detector physics, not as the estimator's angle input. Full exposure/uncertainty
plateau acceptance remains a separate host decision. A leading/trailing lost
status packet cannot erase an endpoint-gated exposure or expected slot.
This is a simulation gate definition, not a claim about a real detector trigger.

Raw intensity/provenance survives missing encoder brackets, status outages,
delayed packets or unmapped clocks. Expected gated exposures, received records,
injected detector losses and pending delivery records are separate. Tests remove
leading, trailing, interior and extended status ranges and compare identical
exposure sequence/intensity against the baseline; association can fail while
all detector records remain available. A separate detector-drop fixture increments
missing. On shutdown, pending raw records are retained as UNDELIVERED_AT_SESSION_END.

The old direct `record(packet)` helper remains for unit fixtures and labels its
metrics HOST_OBSERVED_SLOT_ESTIMATE. Production metrics explicitly say
INDEPENDENT_SIMULATED_DETECTOR. Neither label certifies physical detector losses.
Motion phase, detector capture completeness, association completeness and analysis
status are independent. Capture complete means all expected gated detector records
were retained; it does not mean all associations are valid or the planned scan
completed. Final files are closed and hashed before manifest publication.

## Practical analysis workflow and result sidecar

Run a simulated scan in Rotation lab or the acquisition CLI. Once its motion has
completed and files have finalized, select the resulting `*-optical.csv`:

```bash
.venv/bin/python -B -m polarimeter_rotation_session --simulate --data-dir data
.venv/bin/python -B -m polarimeter_stokes --simulate \
  --optical-csv data/simulated-rotation/YOUR-COMPLETED-optical.csv \
  --zero-offset-deg 5 --analyzer-deg 0 --calibration-id simulated-optical-zero-plus5
```

The +5° here is an **explicit known demo fixture**, never an automatic or physical
calibration. Use the settings belonging to the selected simulated experiment.
The default result file is a unique `*-analysis-<id>.json` beside the optical CSV;
`--analysis-out NEW-PATH.json` chooses another new path. Existing files are never
overwritten. CLI output shows the result path, normalized Stokes/DoP, quality
flags, conditional uncertainties, rank/conditioning, residual and span/gaps.
No analysis runs in Tk or blocks the GUI.

The sidecar stores exact optical CSV SHA256/byte count, acquisition manifest
SHA256, complete acquisition identity, explicit optical-zero/analyzer/calibration
settings and full fit result/covariances. New acquisition manifests retain CSV
hashes; selected files must match them. Hashes are checked before/after analysis.
The immutable acquisition manifest is never edited. Older finalized manifests
without CSV hashes are explicitly labelled LEGACY_MANIFEST_WITHOUT_HASH; the
sidecar still hashes the selected input. Cancelled, UNKNOWN, unfinalized or wrong
experiment inputs are rejected. Motion completion alone is not analysis success.

Only VALID associated exposures enter the normal estimator. It checks canonical
positive uint64 sequences, finite input/design/QR/result values, nonoverlapping
ordered time intervals, consistent signed angular direction, at least 90° valid
midpoint span, rank four and condition at most 1e8. The span threshold is a
quality policy, not a substitute for rank or a physical accuracy guarantee.
Angles beyond ±1e9° fail the numerical input bound. Pathological overflow and
ill-conditioned arithmetic return an error rather than NaN JSON.

S0 and unnormalized Stokes are uncalibrated incident-intensity-equivalent a.u.
in the ideal gain model. Full Stokes and normalized covariances are included.
Optical calibration identity is separate from acquisition's encoder calibration
identity. Uncertainty remains conditional IID intensity noise only, excluding
zero, timing, angle, gain and model systematics. Near zero DoP, the radial
non-Gaussian/ill-conditioned delta-method approximation is flagged
DOP_NEAR_ZERO_DELTA_INVALID and DoP standard error is null. Components' covariance
remains available. DoP above one is retained and flagged, never clipped.
Additional fixtures show small optical-zero sensitivity and nonideal retardance/
analyzer errors; these are model experiments, not actual QWP measurements.

## Native/target conformance boundary

`firmware/main/json.hpp` bounds JSON to four levels, 128 nodes and the existing
1024-byte payload. It rejects duplicate decoded keys, invalid UTF-8/surrogates,
nonfinite numbers, signed-64-bit integer overflow and invalid grammar.
`schema.hpp` admits only the reviewed SIMULATED command schemas with canonical
uint64 strings, scoped identities, strict required/unknown fields and explicit
configuration. Frame admission changes parser counters/last command only.

Each parser vector uses a fresh instance and checks acceptance/error/fault and
inhibited output at chunk widths 1, 7 and 256. Additional traces assert partial
expiry equality, LF resynchronization, oversized chunks and backwards clock.
The deterministic summary separates expected bad inputs from self-test failure.
`app_main` stores separate pass/fail counters if ever separately authorized to
execute; compilation does not execute them.

The portable `Controller` is an **admission/state projection** of the Python
reference, with logical motion intent separate from permanently inhibited mock
outputs. Shared Python-generated oracle traces compare ordered ACKs, state,
arm/config generations, session, lease/motion deadlines, faults and retained
boot/session/arm/motion/time terminal records. Sixteen traces cover 48 cycles:
STOP versus ARM in both orders, multiple changes, exact retries/whitespace-byte
conflicts, heartbeat scope/replay, protocol fault/recovery, lease/duration expiry,
stale session, terminal recovery/STOP after completion, backwards/overflow time
and RX/TX capacity faults. Fixture timing/configuration values are explicit and
are never physical defaults.

This is **not full controller/encoder equivalence**. Feedback freshness,
stationarity/reference/inside gates are trusted fixture projections; count
formation, PCNT coherence/overflow, reference establishment/calibration, physical
trajectory/PI/current behavior and all fault-clear predicates are not target
implemented or comprehensively traced. Untested recovery stays inhibited.
Identity generation in these fixtures is deterministic, not a hardware entropy
source. No target physical backend or live transport exists.

## Executed verification

Pi desktop: **184 Python tests passed**, zero failures/errors/skips, including
**eleven mapped GUI tests**, in 31.792 s:

```bash
POLARIMETER_GUI_TESTS=1 .venv/bin/python -B -m unittest discover -q
```

The actual scan → selected completed CSV → analysis CLI path was also exercised
in a temporary directory. It wrote a sidecar, matched the finalized CSV hash and
left acquisition manifest bytes unchanged. Static syntax/links/whitespace are
checked before publication. No compilation or package installation ran on the Pi.

Hosted [Actions run 46076102923](https://github.com/Benjamin-Kirby-Tennyson/polarimeter/actions/runs/46076102923)
built source commit `4db77750644aaf8e7a18d7205baca96256fbcd73`:

| Layer | Actual result | Limits |
| --- | --- | --- |
| Native C++17 | **352 checks passed, zero failed**, GCC 13.3.0, address/undefined-behavior sanitizers, `-Wall -Wextra -Werror -pedantic` | Hosted x86_64 model, not ESP32 execution |
| Classic ESP32 compile | **PASSED**, ESP-IDF v5.3.4, SDK commit `1b459d9c4950395dec12ce19256c73f9a41e306f`, Xtensa GCC 13.2.0 (`esp-13.2.0_20240530`) | Compile/link only; mock image never flashed/executed |
| Physical validation | **NOT PERFORMED** | No compatibility/safety/measurement claim |

The workflow pins action commits and the official image's manifest digest,
uses read-only repository permissions and ordinary hosted runners, with no
secrets, ports, deployment, flashing or hardware runners. Native/target job caps
are 10/20 minutes. Build logs, compiler/SDK versions, ELF/bin/resource map,
configuration and SHA256 lists are retained as seven-day Actions artifacts.
[Persisted build evidence](build-evidence-20261127.json) records artifact/component
hashes, versions and size report; the downloaded archive SHA256 was checked.
The Actions platform emitted a Node 20 action-runtime deprecation warning and
successfully ran the pinned actions using Node 24; no compiler warning failure
was observed. Action pins should be refreshed in a separate reviewed CI update.

Target image size report: 255292 bytes; static IRAM 51274 bytes, DRAM 11080 bytes.
These map values exclude measured runtime heap/stack high-water behavior. Main
mock self-test stack is configured at 32768 bytes, not measured on hardware.

Binary SHA256:
`2e2995951f96fcf3bdda2e25a933e87697a38b1f338401c277cf05fb66e90e3c`.
Map SHA256:
`ecb2f41d4212b2cd6404e5c4e616a1704959bece31ef33a3cc3818f60b146729`.

## Current simulation and remaining gates

Independent schedule fixture: motion COMPLETED at 38.285 s virtual time; 1200
expected/received detector exposures, 1199 valid associations and one flagged
transition, zero detector losses/pending records, 718.8° contiguous valid midpoint
coverage. Detector capture is complete, association completeness is false due
to the flagged transition, and acquisition reconstruction remains PENDING until
an explicit analysis is selected.

The explicitly +5° calibrated inferred fit gives normalized Stokes approximately
[0.999986, −0.000222, 0], DoP 0.999986, residual 3.54e−8 a.u. The uncalibrated
case remains biased with similarly tiny residual. Analysis flags identify the
excluded exposure. These fixture numbers do not establish physical accuracy.

The pinned SDK is a build baseline, not an operational deployment choice.
Espressif's [support policy](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/versions.html)
and [roadmap](https://github.com/espressif/esp-idf/blob/master/ROADMAP.md)
place the v5.3 line's end of support in January 2027. The
[security dashboard](https://espressif.github.io/esp-idf-security-dashboard/)
was consulted; no complete vulnerability certification of v5.3.4 is claimed.
Review an in-service SDK/patch baseline, applicable advisories and actual board/
flash/partition settings before any future deployment. SDK default compile
settings do not identify the attached board or installed binary.

Next: independent review of this increment; broaden target feedback/reference
and fault-clear conformance; test real scheduling/clock bounds only after staged
commissioning gates. Encoder electrical wiring/counts, optical zero/instrument,
motor/driver current/thermal capacity, independent default-inhibited energy
isolation and installed firmware compatibility remain unresolved. No live UART,
PCNT allocation, PWM, motor drive, flashing or merge is authorized here.
