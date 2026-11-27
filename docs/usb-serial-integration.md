# ESP32 USB serial integration

Phase 2 metadata discovery, the architecture/assumptions audit, physical wiring
checks and the staged passive-observation plan are recorded in
[Hardware discovery and integration readiness](hardware-readiness.md).

The [shared ChatGPT hardware context](hardware-integration-context.md) records
the latest reported motor, encoder and driver wiring. The
[Codex audit response](codex-integration-audit.md) records what the current
source implements and which reported settings remain unverified.

## Verified host discovery, 2026-10-27

These checks inspected device metadata, symlinks and permissions only. They
did not open the serial port, reset the ESP32, transmit data, flash firmware,
energize the motor or change device permissions.

| Property | Observed value |
| --- | --- |
| Bridge | Silicon Labs CP2102 USB-to-UART |
| VID:PID | `10c4:ea60` |
| USB serial | `0001` |
| USB location | `1-1.2` |
| Driver | `cp210x` |
| Current node | `/dev/ttyUSB0` |
| Ownership and access | `root:dialout`, `0660`, no additional ACL |
| Active host groups | Includes `dialout` |
| Sandbox | Hides device nodes and omits host supplementary groups |

Preferred persistent identifier, provided its serial number is unique among
attached bridges:

```text
/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0
```

Location-specific identifiers, tied to this physical USB topology:

```text
/dev/serial/by-path/platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.2:1.0-port0
/dev/serial/by-path/platform-fd500000.pcie-pci-0000:01:00.0-usbv2-0:1.2:1.0-port0
```

All three currently resolve to `/dev/ttyUSB0`. Device numbers can change;
by-path changes if the board moves ports. Bridges can share a serial number
such as `0001`, so future selection must reject ambiguity and verify identity
and location. USB identity establishes the bridge, not the application firmware.
The user's Waveshare touch confirmation is independent of serial validation.

Repeat these metadata checks from a normal Pi terminal without opening ports:

```bash
id
ls -l /dev/ttyUSB0 /dev/serial/by-id/ /dev/serial/by-path/
getfacl -p /dev/ttyUSB0
udevadm info --query=property --name=/dev/ttyUSB0
```

The new `discover_usb_devices()` helper uses pyserial's enumeration API and
resolves symlinks; it never constructs `serial.Serial`. It returns USB metadata
without selecting or probing a device. It reports an empty tuple if no USB
serial devices are visible; enumeration exceptions propagate. An empty result
inside the sandbox does not prove the host has no board. No chmod, udev rules,
root application execution or group changes are needed for the verified host.

## Existing code and firmware inspection

- `polarimeter_core.Motor` defines `angle_deg`, `move_to()` and `stop()`;
  `Detector` defines `read_intensity()`. These are instrument interfaces, not
  serial adapters. Only simulated and locked implementations exist.
- `LockedMotor.stop()` is a local no-op. It is not an emergency stop.
- `ScanController` and the GUI use locked adapters unless simulation is
  explicitly selected. No transport is attached to the GUI by this change.
- The legacy CLI contains a read-only newline/UTF-8 reader and float/CSV/JSON
  parsers. It previously opened ports given explicit arguments. A new early
  guard rejects hardware acquisition before any serial import, port access or
  output creation. Its parser formats are not evidence of the ESP32 protocol.
- No firmware source or build manifest was found in tracked project files or
  the searched ordinary user folders and mounted storage. Searches covered
  `.ino`, `.c`, `.cpp`, `.h`, `platformio.ini`, `sdkconfig` and
  `idf_component.yml`; caches, tool environments and Codex internals were
  excluded. Documents and Downloads were empty. No firmware files were changed.
- The user confirmed that firmware source and protocol documentation are
  currently unavailable. No installed firmware was read back or interrogated.

## Implemented communication boundary

`polarimeter_serial.py` uses the standard library. Pyserial is imported lazily
for metadata discovery only. There are no additional dependencies, background
threads, GUI changes, motor commands, handshake guesses or writes.

The `ReceiveBackend` contract has `mode`, `connect()`, nonblocking bounded
`read(size)` and `close()`. `USBSerialBackend` holds discovery identity and is
hard-locked: connect/read raise `HardwareLockedError`. The session layer also
refuses to connect a backend labelled `HARDWARE LOCKED`. There is no unlock
flag, firmware acknowledgment shortcut, default baud rate or physical I/O
implementation hidden behind simulation. A reviewed physical adapter remains
pending firmware and safety validation.

`MockSerialBackend` accepts explicit byte input, can simulate an unplug, and
has a bounded input buffer. All frames and status events carry `SIMULATED`.
It cannot drive the real motor/detector adapters or enter their CSV logger.

`SerialConnection` provides:

- One bounded read per `poll()`, with explicit delimiters and opaque byte
  payloads. Fragmented/coalesced frames and multibyte delimiters are supported.
  Binary bytes are preserved without guessing an encoding or measurement schema.
- A maximum payload size, bounded pending-frame capacity and bounded status
  history. Defaults are 4096 payload bytes, 128 queued frames, 32 status events,
  a 4096-byte read and a 65536-byte mock input buffer. Queue overflow faults
  closed; it never silently discards the oldest sample to keep running.
- `DISCONNECTED`, `CONNECTED`, `STALE`, `FAULT` and `LOCKED` states. Connected
  means local transport only; neither connection nor byte receipt establishes
  a valid firmware handshake. The mock defaults mark stale at 2 seconds and
  fault at 10 seconds without a complete frame; these are not ESP32 timings.
- Stale recovery on a complete frame, host-monotonic receipt timestamps,
  session IDs and per-session sequence numbers. Host receipt time is not
  device sampling time. Partial-frame traffic does not hide a timeout.
- Fault cleanup on connect/read exceptions, oversized frames, broken backend
  contracts, queue overflow and frame timeout. Pending/partial data are cleared
  on fault or disconnect, so callers cannot mistake them for a fresh session.
  The error reason remains in status. These are receive-session faults, not
  verified application-protocol errors or safe motor-stop actions.
- Explicit reconnect only. No automatic reopening or simulation fallback.
  Close failures remain visible and block reconnect until a retry of local
  disconnect succeeds. Closing a USB transport does not establish motor safety.

Single-owner polling avoids concurrent mutation; the caller must poll regularly
and consume frames. No worker is needed for the nonblocking mock. Future
physical I/O and parsing must stay outside Tk callbacks and hand results to the
GUI through a bounded queue, after protocol/safety review.

This complete example uses only mock bytes and creates no measurement files:

```bash
cd /home/cuqit/polarimeter
.venv/bin/python -B - <<'PY'
from polarimeter_serial import Framing, MockSerialBackend, SerialConnection

backend = MockSerialBackend()
session = SerialConnection(backend, Framing(delimiter=b"\n"))
try:
    assert session.connect()
    backend.feed(b"mock frame one\nmock frame two\n")
    print(session.poll())
    while (frame := session.take_frame()) is not None:
        print(frame.mode, frame.session, frame.sequence, repr(frame.payload))
finally:
    session.disconnect()
PY
```

The sample newline framing and text are mock fixtures, not a proposed firmware
protocol. Detector values remain uncalibrated arbitrary units, and no real
encoder angle, optical calibration or Stokes reconstruction has been implemented.

## Information still required

| Area | Required evidence before physical integration |
| --- | --- |
| Firmware identity | Installed build and behavior remain unknown; source/documentation are unavailable. Future isolated passive evidence must be preserved and validated before any parser or control implementation |
| UART configuration | Baud, data bits, parity, stop bits, flow control; CP2102 identification does not establish these |
| Reset/startup | CP2102 DTR/RTS wiring to EN/boot, effects of open/close/reset and USB reconnect, boot defaults and any automatic movement |
| Framing and messages | Binary/text format, delimiter or length, maximum size, checksums, message types, boot/error records, command acknowledgments and version checks |
| Acquisition | Passive streaming vs explicit requests, start/init requirements, detector field/schema/range/units, sample timing/rate and correlation with encoder data |
| Encoder | Type, counts/revolution, quadrature convention, gearing/coupling, direction, index/zero, angle wrap, missed-count behavior and measured QWP-angle calibration |
| Motion | TB6612FNG driver reported by user on 2026-11-26; actual channel, ESP32 GPIO wiring, standby handling, motor ratings, homing, travel limits, units, speed/acceleration, error handling and movement acknowledgments remain unverified |
| Safe stop | Verified stop semantics, physical power cut, reset/USB-loss behavior, firmware watchdog and limits; a closed serial port is not evidence of a stop |

## Next safe hardware validation step

Keep the port closed. Firmware source and protocol documentation are known to
be unavailable; no further request for them is needed at this stage. The next
milestone is a physical wiring record and verified motor energy isolation,
including reset/boot circuitry and an independent emergency stop, followed by
a passive-observation readiness review. No physical checks are certified by
this software work.

The [Phase 2 plan](hardware-readiness.md) describes a later, separately
authorized one-way external UART observation with no drive connection to ESP32
RX, EN, boot or motor-control lines and no DTR/RTS connection. Offline analysis of observed bit timing
may establish UART settings without guessing or opening CP2102. If the board
is silent, do not provoke traffic with a command or reset. Do not brute-force
baud rates, use terminal programs, run esptool, upload a test sketch or bypass
the existing serial lock. A direct physical USB receive adapter remains
deferred pending the reset/protocol/encoder/stop gates. The GUI's hardware
controls remain disabled throughout discovery and passive observation.

Pyserial documents both [enumeration metadata](https://pyserial.readthedocs.io/en/latest/tools.html#serial.tools.list_ports)
and [port opening and possible RTS/DTR glitches](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.open).
Setting DTR/RTS false is not a guarantee against resetting an ESP32.

## Validation

The 21 tests in `tests/test_serial.py` use temporary metadata, fake byte streams,
injected exceptions and an injected monotonic clock. They cover both lock
boundaries, legacy CLI bypass prevention, enumeration without constructing a
serial driver, frame fragmentation and limits, explicit reconnect/session
provenance, bounded queues/history, stale/timeout/unplug and cleanup failures.

The complete opt-in suite passed 49 tests with no skips: the original 28 plus
21 serial tests, including all five mapped Tk tests and six desktop health
tests. Syntax checks and whitespace checks passed. The GUI source, core optics and
controller source, and all original tests are unchanged. GUI tests use synthetic
events; they do not constitute new physical touch or motor validation.

```bash
.venv/bin/python -B -m unittest discover -v
# Opt-in from the Pi desktop; briefly maps simulation-only Tk test windows.
POLARIMETER_GUI_TESTS=1 .venv/bin/python -B -m unittest discover -v
```

No firmware was modified, no physical serial port was opened, and nothing was
committed or pushed for this integration change.
