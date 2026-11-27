# Rotating quarter-wave plate polarimeter

A lightweight 640×480 Tkinter touchscreen interface for a Raspberry Pi 4 with
1 GB RAM. It displays QWP angle, uncalibrated optical intensity, and a live
intensity-versus-angle plot. Physical control remains disabled. Command-line
simulation and parser behavior are preserved; hardware acquisition is locked.

Phase 2 [hardware discovery and integration readiness](docs/hardware-readiness.md)
records the current USB inventory, communication-code audit, physical wiring
checklist, passive-observation plan and future measurement/encoder timing design.
The CP2102 and touchscreen are identified; no separate USB optical measurement
device is positively identified. The latest [Stage 4 report](docs/offline-rotation-stage4.md)
records the simulated rotation workflow and its 153 passing tests, including GUI tests.

## Launch the touchscreen interface

From the Pi desktop:

```bash
cd /home/cuqit/polarimeter
.venv/bin/python polarimeter.py --gui --simulate
```

Simulation is explicit: the mode banner, plot title, terminal messages, CSV
records and filenames all say **SIMULATED**. Without `--simulate`, the GUI
shows **HARDWARE LOCKED**, displays no instrument readings and disables scan,
jog and target controls. It never opens a serial port or power meter, sends
motor commands, flashes firmware or falls back to simulation.

- **Start Scan:** create a new CSV and scan model angles 0–360° inclusive in
  2° steps (181 points), at a nominal 20 samples/s. Completion closes the CSV.
- **Stop:** cancel the local scan timer and close/flush the CSV. This is not a
  physical emergency stop or a transmitted stop command.
- **CW / CCW:** move the simulated angle by +5° / −5°, wrapping at 360°.
  These are software conventions, not verified physical motor directions.
- **Target:** open the built-in numeric keypad, enter 0–360°, and tap **Set**.
  **Go** moves the model and logs a reading. No desktop keyboard is needed.
- Manual readings share a CSV until Stop, a new scan, or Exit. Manual controls
  are disabled while scanning. No measurement file is created just by opening
  the application or checking touch.
- **Rotation lab:** open the separate simulated motor/encoder/PI workflow.
  **Start 3-turn scan** performs one bounded 0–1080° transaction with a
  constant-speed acquisition plateau, streamed encoder/exposure CSV and a session
  manifest in `data/simulated-rotation/`. Settings are invented simulation fixtures,
  not hardware tuning. Requested angle, encoder estimate and optical truth remain
  separate; Stokes reconstruction is pending. **Cancel scan** requests local
  simulated output inhibition; it does not establish physical stopping.
- **Check touch**, inside **Motor / USB:** stop local acquisition and show five
  finger/pointer targets.
- **Motor / USB:** show USB serial bridge metadata, with a manual **Refresh USB**
  button. Discovery never opens a port, sends commands, or enables physical
  controls. A detected bridge does not identify the motor firmware. The panel
  works in both simulation and hardware-locked mode; it creates no measurements.
- **Exit** closes the app; Escape dismisses an overlay or exits.

The default is fullscreen to avoid desktop panels and window decorations on
this small screen. All controls fit in 640×480; USB metadata can scroll. Buttons are
at least 44 pixels high; fonts use pixel sizes to avoid desktop DPI expansion.
`--windowed` is available for development on a larger desktop. On a larger
screen, the 640×480 interface is centered.

The GUI uses Python's standard library and Tk, including a Tk Canvas plot.
Only opening or refreshing **Motor / USB** imports pyserial for metadata
enumeration. It does not import NumPy, SciPy, Matplotlib or a vendor SDK.
History is bounded (default `--window 1000`, allowed 2–2000); plot redraws are
capped at 5 Hz. `--rate` accepts 1–50 Hz. Tk timers are not precision acquisition
clocks: a slow UI delays samples rather than queuing catch-up moves.
The Rotation lab owns its virtual clock, protocol and disk work in a separate
worker; its plot history is capped at 1000 exposure points. It runs independently
of Tk polling but does not demonstrate an ESP32 hardware watchdog or real-time
timing. No live serial backend is enabled.

For an accelerated, explicitly simulated end-to-end run without Tk:

```bash
.venv/bin/python -B -m polarimeter_rotation_session --simulate --data-dir data
```

## Display and finger-input verification

On 2026-10-27 the user confirmed that the Waveshare touchscreen is physically
working, including **finger input in the polarimeter GUI**. This is human
hardware verification; automated pointer events alone cannot establish it.

Display checks from GUI development on 2026-10-27 found:

- Tk and `xrandr --current` both report **640×480**. HDMI-A-1 is connected;
  `xrandr` reports approximately 74.77 Hz. No display configuration was changed.
- Tk 8.6 successfully created and rendered the interface on the current
  Wayland/Xwayland desktop. The fullscreen window measured 640×480.
- A separate interactive input-check window received all five pointer targets.

Current read-only USB/kernel checks identify a **WaveShare WS170120** touch
controller (`0eef:0005`, serial `220211`), bound to `hid-multitouch`.
The sandbox hides `/dev/input`; this is a session visibility limitation.

To repeat the application input check without changing configuration or
probing devices:

```bash
cd /home/cuqit/polarimeter
.venv/bin/python polarimeter.py --touch-check
```

Tap each numbered target with a finger, including the screen corners. This
checks pointer delivery and positioning in the actual application. Mouse
clicks and automated events also register, so the program reports pointer
input, not a claim of hardware touch verification. A human must confirm finger
use. There is no calibration, driver installation, resolution change or input
configuration write. If taps fail, inspect the display's exact touch-controller
connection and driver from a normal Pi terminal before changing configuration.

## Simulation optics and interpretation

The simulated optical path is fully polarized input → ideal rotating QWP →
fixed ideal linear analyzer → ideal intensity detector. The default input is
horizontal linear polarization `(Ex, Ey) = (1, 0)`, the analyzer is horizontal,
and the retardance is π/2. No random waveform or detector noise is added.

For model angle θ in the lab x/y basis:

```text
u = (cos θ, sin θ)             # QWP fast axis
v = (−sin θ, cos θ)            # QWP slow axis
J(θ) = u uᵀ + exp(i δ) v vᵀ   # δ = π/2 for an ideal QWP
Eout = J(θ) Ein
I = |(cos α, sin α) · Eout|²   # α = fixed analyzer angle
```

This convention specifies the relative phase explicitly; global phase has no
intensity effect. The defaults give `I(θ) = 1 − 0.5 sin²(2θ)`, between 0.5 and
1 arbitrary units. Removing the analyzer would give constant total intensity
through a lossless QWP. Reference for Jones vectors and waveplate/analyzer
matrices: [Thorlabs polarization tutorial](https://media.thorlabs.com/globalassets/family-pages/SharedAssets/p/po/Polarization_Handedness_Tutorial.pdf).

`JonesModel` also accepts complex input components, analyzer orientation and
retardance in code; its norm sets incident intensity. It models fully polarized
light only. The displayed angle is a **model angle**, not measured position or
verified physical motion. Intensity is **uncalibrated arbitrary units**, not
watts. Optical calibration, waveplate angle measurement, Stokes reconstruction,
partial polarization and real detector acquisition are not implemented.

## CSV measurement logging

GUI files are exclusive, timestamped, randomly suffixed and never overwritten:

```text
data/simulated/simulated-qwp-<UTC timestamp>-<suffix>.csv
```

Use `--data-dir /path/to/data` to choose a different base directory; the
`simulated/` subdirectory is always added. Every record includes:

- Host UTC timestamp and monotonic elapsed seconds within the file.
- `mode=SIMULATED`, `qwp_angle_deg`, `angle_source=SIMULATED_MODEL_ANGLE`.
- Intensity, `intensity_unit=a.u. (uncalibrated)`, and
  `detector_source=SIMULATED_JONES`.
- Model version, input Ex/Ey real and imaginary components, analyzer degrees,
  and retardance radians, so the simulation can be reproduced.

CSV streams to disk, with a flush approximately every second and close on
Stop, completion, Exit or handled acquisition error. Hard power loss can lose
buffered records. Disk use grows with acquisition time. Errors are shown in
the interface and terminal and stop local acquisition; logging failure cannot
silently continue an unrecorded scan. Real acquisition must use its own
hardware directory, provenance and units; this logger only writes simulation.

## Code and future instrument adapters

- `polarimeter.py`: entry point and preserved command-line acquisition.
- `polarimeter_gui.py`: Tk layout, bounded Canvas rendering, keypad, pointer
  check and cancellable event-loop timers. No hardware transport imports.
- `polarimeter_core.py`: Jones optics, `Motor` / `Detector` interface contracts,
  simulated adapters, locked hardware placeholders, scan lifecycle and CSV.
- `polarimeter_serial.py`: enumeration-only USB discovery, a hard-locked USB
  backend, labelled mock byte streams and a bounded receive/connection monitor.
  See [USB serial integration](docs/usb-serial-integration.md) for the API,
  verified device paths, fault behavior and firmware prerequisites.
- `tests/`: model, controller, CSV, error, CLI regression and optional Tk tests;
  root discovery also runs the existing `desktop-setup` health tests.

The hardware placeholders raise on motion/acquisition requests. No option can
unlock them. The serial receive/session layer is implemented against mocks;
its physical USB backend cannot open a port. The legacy CLI also rejects all
hardware acquisition before opening ports or output files. Firmware source and
protocol documentation are unavailable. The Phase 2 plan establishes a path
toward future passive observation after physical isolation checks, without
guessing settings or opening the existing port. Real adapters still require
verified UART/framing/motion behavior and an identified optical device/API.
Verify homing, travel limits, angle feedback/provenance,
timeouts, disconnect handling, stop behavior, detector units/calibration and
angle/intensity timing. Blocking I/O must stay out of Tk callbacks and use a
bounded queue when it is eventually introduced. Do not guess protocols or
activate hardware during setup. Opening an ESP32 port can reset it even with
DTR/RTS disabled. Discovery must enumerate ports only.

## Environment and dependencies

Verified on 2026-10-27: Debian 13.7 ARM64, Python 3.13.5, about 905 MiB RAM,
Tk 8.6, NumPy 2.2.4, pyserial 3.5, SciPy 1.15.3 and Matplotlib 3.10.1.
`.venv/pyvenv.cfg` enables system site packages. No dependencies or system
configuration were changed for the GUI. Earlier setup notes about missing
SciPy/Matplotlib describe the initial 2026-10-24 environment, not this check.

If recreating the environment from a normal Pi terminal:

```bash
cd /home/cuqit/polarimeter
sudo apt update
sudo apt install --no-install-recommends python3-venv python3-tk
python3 -m venv --system-site-packages .venv
.venv/bin/python -c 'import tkinter; print(tkinter.TkVersion)'
```

The original CLI's plotting/serial features additionally use Debian's
`python3-matplotlib python3-serial`; `requirements.txt` preserves the original
scientific dependencies for that workflow. Prefer Debian prebuilt packages.
If using pip, use `.venv/bin/python -m pip install --only-binary=:all:` with the
needed package names. Stop if wheels are unavailable; do not compile packages
on this Pi, pip-install into system Python or use `--break-system-packages`.
No pip packages are necessary for the new Tk GUI.

## Tests and verification

```bash
cd /home/cuqit/polarimeter
.venv/bin/python -m unittest discover -v
# Opt-in: briefly opens windows on a real desktop; synthetic button actions only.
POLARIMETER_GUI_TESTS=1 .venv/bin/python -m unittest discover -v
```

The headless suite checks analytical linear/circular Jones results, lossless
energy conservation, analyzer bounds, invalid input, full scan completion,
CSV provenance, stop/restart, bounded history, disk/detector errors, locked
hardware and original CLI/parser behavior. The GUI tests check 640×480 bounds,
keypad entry, synthetic pointer-target delivery, timer cancellation, error
messages and disabled physical controls. These tests do not open serial ports
or verify motors, power meters or physical finger touch.

A mapped fullscreen simulation on 2026-10-27 completed all 181 points, streamed
a labelled CSV, and used about 25 MiB peak process RSS. A screenshot was
inspected for layout fit. This is a short simulation measurement, not a
long-duration resource or hardware validation.

On 2026-11-27 the complete opt-in suite passed **153 tests with no skips**,
including ten mapped-window GUI tests. It covers the original optics/CLI and
desktop-health behavior, serial locks/mocks, bounded codec, controller faults,
simulated plant/PI, framed host, exposure timing, streamed provenance and worker
shutdown. Headless discovery intentionally skips the ten mapped-window tests.
Source syntax, documentation links and `git diff --check` passed. No tests open
a physical serial port or establish electrical/motor/optical readiness.

## Preserved command-line acquisition

These original commands remain available. The original CLI simulation is a
synthetic time waveform, distinct from the new GUI's Jones model; its original
CSV schema remains `timestamp_utc,elapsed_s,mode,detector_value`.

```bash
# Enumeration only; never opens a serial port.
.venv/bin/python polarimeter.py --list-ports
# Original synthetic CLI; no plotting dependency.
.venv/bin/python polarimeter.py --simulate --no-plot --seconds 5
# Original live time plot.
.venv/bin/python polarimeter.py --simulate --window 1000 --rate 20
# Original headless PNG and CSV (choose unused output names).
.venv/bin/python polarimeter.py --simulate --headless --seconds 5 --window 50 --output data/simulation-check.csv --plot-file data/simulation-check.png
```

Legacy hardware acquisition is now hard-locked even with explicit `--port`,
`--baud` and `--format`; no runtime option unlocks it. The existing float/CSV/JSON
parsers remain available and tested, but are not a verified ESP32 protocol.
GUI mode continues to reject all serial settings. Simulation behavior and CSV
formats are unchanged.

Updated read-only host checks on 2026-10-27 confirmed `/dev/ttyUSB0`, bridge
VID:PID `10c4:ea60`, serial `0001`, USB location `1-1.2` and driver `cp210x`.
The node is `root:dialout` with mode `0660`; the host session has active
`dialout` membership. Both `/dev/serial/by-id/` and `/dev/serial/by-path/` links
resolve to the device; exact paths are in the integration notes. Earlier
no-device results predate this attachment. The sandbox still hides the node
and omits `dialout`, so check permissions on the host. No device permissions
were changed, and no serial port was opened. Firmware and UART settings remain
unknown. Do not run the application as root or make device nodes world-writable.
