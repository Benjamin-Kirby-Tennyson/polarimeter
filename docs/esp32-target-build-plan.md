# ESP32 target plan: initial proposed baseline

Historical plan at `058225c`. [Stage 5](offline-stage5.md) records the subsequent
strict admission/state projection, **352 passing native checks and successful
hosted ESP32 compile**. The NOT COMPILED status and envelope-only limitations
below describe the initial skeleton. No target execution or physical validation
has occurred.

2026-11-27. **PROPOSED, NOT COMPILED, NOT FLASHED, NOT AS-BUILT.**
See [canonical reported hardware](hardware-integration-context.md) and
[protocol revision 0.2](proposed-motor-protocol-rev02.md). Installed firmware
identity/protocol remain UNKNOWN. Nothing here opens a serial port or provides a
physical output backend. Do not use `idf.py flash`, `monitor` or port discovery
that opens a port. Existing application hardware locks remain in force.

## Explicit target baseline

Propose classic ESP32 (`IDF_TARGET=esp32`, Xtensa LX6, reported ESP32-D0WD-V3),
ESP-IDF **v5.3.4**, C++17. Exact development-board model/flash/partition settings
are UNKNOWN. This pinned software review baseline is not a claim about the
installed sketch or a recommendation to deploy an older SDK. Confirm support,
security/errata and board settings before any later operational firmware.

`firmware/` is a minimal ESP-IDF CMake project: compile-only mock outputs,
64-bit microsecond clock abstraction, unconfigured PCNT source, bounded frame
envelope/parser and shared vectors. `app_main` initializes no UART, GPIO, LEDC,
PCNT or motor peripheral. Its mock enable method always refuses; no conditional
physical enable switch exists. No encoder pin/count, electrical level, PWM
frequency/resolution, PID gain or UART baud is supplied.

The skeleton checks envelope length/trailer/CRC and bounded chunk/partial-frame
time. It deliberately **does not implement strict JSON or command admission**;
a valid envelope cannot authorize anything. Shared vectors distinguish envelope
acceptance from strict host JSON acceptance (a duplicate JSON key has valid CRC
but must fail JSON validation). This is not full host/target conformance.

## Availability and build boundary

Read-only checks on this Pi found no `idf.py`, `arduino-cli`, `pio`,
`xtensa-esp32-elf-g++`, Arduino cache or PlatformIO cache. A native `/usr/bin/g++`
is present; native compilation is not an ESP32 build. No toolchain/package was
installed and **no compilation was attempted**, preserving the project's
no-compilation-on-Pi rule and low RAM budget. Exact SDK/CMake link correctness,
compiler warnings, stack/heap use and target self-tests remain unverified.

On a separately provisioned build computer, after reviewing the pinned board
and SDK configuration, the future compile-only command is:

```bash
# In an ESP-IDF v5.3.4 environment on the separate build computer:
idf.py -C firmware -DIDF_TARGET=esp32 build
```

This command creates a binary; it must not be combined with flashing or opening
ports. Archive SDK/toolchain commit/version, configuration, compiler output,
artifact SHA256 and resource map. Provisioning or executing that build is still
outstanding. Generate byte-identical shared fixtures locally, without building:

```bash
.venv/bin/python -B firmware/tools/generate_vectors.py
```

Python tests execute the host side and verify generated target fixture bytes.
Target execution/compile results must be reported separately, never inferred
from passing Python tests.

## Incremental implementation and verification

| Component | Required target contract / evidence | Current skeleton |
| --- | --- | --- |
| Clock | `esp_timer_get_time()` read, signed failure, fresh boot identity, checked uint64 deadline addition, backward/overflow inhibition | Read wrapper and backward/partial deadline check; no full controller |
| PCNT | Explicit A/B pins/voltage/decoding, coherent count+time snapshot, overflow/watchpoint handling and checked wider count, stale/discontinuity rejection | Interface returns invalid; no peripheral allocation |
| JSON/framing | 1024-byte payload, 1050-byte line, 256-byte chunks, depth 4, 128 nodes, strict UTF-8/duplicate keys/finite numeric bounds, exact request bytes, bounded LF resynchronization | Envelope/CRC only; JSON decoder unresolved |
| Controller | Preflight whole bounded batch, fault/STOP precedence, no post-stop enable, config/reference/challenge/arm leases, retained identity-bound terminal and explicit recovery, exact retry cache | No dispatcher; all output requests refused |
| Scheduling | Independent device polling, bounded RX/TX work, dedicated control deadline, fresh encoder age, heartbeat lease, overrun fault; measure worst-case latency | No task or real-time claim |
| Outputs | Default inhibited from boot, independent energy disable, verified bridge modes, defined unused channel, current/thermal/transient limits, reset behavior, reversal policy | Mock only; no GPIO writes |
| Host mapping | Real monotonic host/device synchronization with explicit uncertainty; no reuse of simulator virtual clock | Not implemented |

The SDK PCNT driver supports edge/level quadrature decoding and accumulation
across hardware counter limits. Its accumulation is not proof of an indefinitely
wide physical count: choose watchpoints/overflow extension, interrupt budget,
maximum edge rate and integrity checks after measurements. See the pinned
[PCNT API](https://docs.espressif.com/projects/esp-idf/en/v5.3.4/esp32/api-reference/peripherals/pcnt.html).
The [ESP timer documentation](https://docs.espressif.com/projects/esp-idf/en/v5.3.4/esp32/api-reference/system/esp_timer.html)
describes microsecond timestamps and callback scheduling limitations. A timer
API does not establish a control-task or physical stopping deadline.

Next conformance increment: run shared CRC/envelope vectors against a compiled
native mock runner on the separate build computer, add timeout/chunk/overflow/
backward-clock vectors, then strict JSON vectors and controller event traces
including duplicate requests, faults before completion, lost terminal recovery,
heartbeat rejection and STOP at capacity. Compare ordered outcomes and final
state, not just acceptance. Target skeleton cannot claim these tests yet.

## Physical blockers remain

STBY is reported tied high. A firmware inhibit is not independent energy
isolation. Encoder pins/supply/logic, counts/decoding/gear ratio, optical zero,
detector interface, motor/driver current and thermal suitability, unused-channel
termination and installed firmware compatibility remain unresolved. Later
physical commissioning requires verified isolation and separate explicit
hardware authorization; offline implementation approval does not supply it.
