# Detector / rotating-quarter-wave-plate acquisition

Small Python application for USB serial discovery, timestamped CSV logging and
live detector plotting. Simulation is explicitly labelled SIMULATED in the
terminal, CSV mode column, plot title and default filenames. The waveform is
synthetic, not an optical calibration or reconstruction. No firmware or motor
commands are included. No serial port was opened during setup.

## Setup status and dependencies

Inspected 2026-10-24: Debian 13.7 ARM64, Python 3.13.5, 904 MiB RAM,
about 320 MiB available and 21 GiB free disk. Project initially empty.
`.venv` was created with system packages enabled; NumPy 2.2.4 and pyserial 3.5
import successfully. SciPy and Matplotlib are **not yet installed**. Binary-only
pip installation failed because package server DNS was unavailable in this
restricted session. Sudo is also unavailable here. No system Python pip changes
were made.

From a normal Pi terminal with network and sudo, use Debian prebuilt packages:

```bash
cd ~/polarimeter
sudo apt update
sudo apt install --no-install-recommends python3-venv python3-numpy python3-scipy python3-matplotlib python3-serial python3-tk
# Already created; this command also documents how to recreate it:
python3 -m venv --system-site-packages .venv
.venv/bin/python -c "import numpy, scipy, matplotlib, serial; print(numpy.__version__, scipy.__version__, matplotlib.__version__, serial.__version__)"
```

Alternatively, if system NumPy/pyserial remain installed, install only missing
packages into the virtual environment using binary wheels:

```bash
cd ~/polarimeter
.venv/bin/python -m pip install --only-binary=:all: --no-cache-dir scipy matplotlib
```

Stop if binary packages are unavailable; do not compile or use
`--break-system-packages`. `requirements.txt` records the four dependencies.
The application does not import SciPy at startup; it is prepared for later
analysis once installed.

## Exact launch commands

Discover ports (enumeration only, no opening):

```bash
cd ~/polarimeter
.venv/bin/python polarimeter.py --list-ports
```

Live simulation from the Pi desktop (close the window or press Ctrl+C to stop):

```bash
cd ~/polarimeter
.venv/bin/python polarimeter.py --simulate --window 1000 --rate 20
```

Headless simulation and PNG verification, after Matplotlib is installed:

```bash
cd ~/polarimeter
.venv/bin/python polarimeter.py --simulate --headless --seconds 5 --window 50 --output data/simulation-check.csv --plot-file data/simulation-check.png
```

Output files are exclusive: rerunning with the same CSV filename fails instead
of overwriting. Choose another filename or omit `--output` for a timestamped
name. The PNG contains the last `--window` samples, while CSV contains the entire
run. The in-memory deques hold at most 1000 samples by default; display updates
are capped at approximately 5 Hz. Disk usage grows with the run. CSV is flushed
at approximately one-second intervals during valid acquisition and on normal
exit. Hard power loss can lose buffered data.

Logging without plotting works even before Matplotlib is installed:

```bash
cd ~/polarimeter
.venv/bin/python polarimeter.py --simulate --no-plot --seconds 5
```

CSV columns: timestamp_utc (host UTC ISO 8601), elapsed_s (host monotonic clock),
mode, detector_value. These are host receive times, not ESP32 acquisition times.
Values have arbitrary units. Rate controls simulation only.

## Hardware configuration — inspect firmware first

There is deliberately no default port, baud rate or input format. Inspect the
existing ESP32 firmware or its documentation before choosing them. No firmware
source was present. This reader supports UTF-8 newline-terminated records:

- `--format float`: one number per line.
- `--format csv --column 1`: number in the second CSV field (zero-based).
- `--format json --key detector`: number in the JSON `detector` field.

Malformed, non-finite, incomplete and oversized records are skipped. Frames
are limited to 4096 bytes. Binary formats need a parser after inspection.

Once the protocol is known, set these shell variables to the verified values
(the shell rejects missing variables rather than choosing defaults):

```bash
cd ~/polarimeter
: "${SERIAL_PORT:?Set SERIAL_PORT to the discovered device path}"
: "${SERIAL_BAUD:?Set SERIAL_BAUD from inspected firmware}"
: "${SERIAL_FORMAT:?Set SERIAL_FORMAT to float, csv, or json after inspection}"
.venv/bin/python polarimeter.py --port "$SERIAL_PORT" --baud "$SERIAL_BAUD" --format "$SERIAL_FORMAT"
```

Add `--column N` or `--key FIELD` when appropriate. Prefer a stable
`/dev/serial/by-id/...` path when available. The application performs reads only,
sets DTR/RTS false before opening, and never writes serial data. Drivers can
still pulse control lines or reset an ESP32 during opening. Confirm that this
is acceptable before hardware use. Angle synchronization, motor control and
Stokes reconstruction are outside this setup.

## USB permissions

No `/dev/ttyUSB*`, `/dev/ttyACM*` or `/dev/serial/by-id` device was visible in
this session; the restricted environment may hide host devices. On the actual
Pi terminal, connect the ESP32 using a data-capable USB cable and run:

```bash
id
getent group dialout
ls -l /dev/serial/by-id/ /dev/ttyUSB* /dev/ttyACM*
cd ~/polarimeter
.venv/bin/python polarimeter.py --list-ports
```

The group database already lists `cuqit` in `dialout`, but this session's active
groups omit it. Log out of the Pi desktop/SSH session completely and log back
in, then verify `id` includes `dialout`. If the normal host's group database
does not list your account, run `sudo usermod -aG dialout cuqit`, then log out
and back in. Do not run the app as root or make device nodes world-writable.
If devices remain hidden from this app session, use the host terminal.

## Verification performed

- Python syntax compilation passed.
- NumPy and pyserial imports passed; SciPy/Matplotlib imports remain blocked.
- Port discovery ran and reported no visible devices.
- A two-second logging-only simulation generated 40 records in
  `data/verification.csv`; UTC timestamps, increasing elapsed times and explicit
  SIMULATED labels were checked.
- Float, CSV and JSON parsers passed checks, including non-finite rejection.
- Plot rendering and GUI interaction remain unverified because Matplotlib is
  missing. Run the headless command above to verify PNG generation, then the
  desktop command to verify a live window. Hardware behavior is untested.

`pip check` also reports unrelated incomplete system package/stub dependencies
inherited through `--system-site-packages` (including Flask stubs and
apt-listchanges). These were not changed; project dependency status is based
on direct imports rather than claiming the entire system environment is clean.
