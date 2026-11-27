# Project context

Work in /home/cuqit/polarimeter. Raspberry Pi, Debian 13 ARM64, Python 3.13,
904 MiB RAM observed. Keep dependencies and runtime memory lightweight.
Hardware: ESP32, photodiode detector, rotating quarter-wave plate.
Existing firmware, baud rate, serial framing and motor interface are UNKNOWN.
Do not flash firmware, guess baud rates, transmit serial data or issue motor
commands during setup. Inspect firmware/protocol before hardware acquisition.
Opening serial ports can reset ESP32 boards despite disabled DTR/RTS.
Discovery must only enumerate ports; never probe by opening them.

Use .venv with --system-site-packages to reuse Debian prebuilt packages.
Never pip-install into system Python or use --break-system-packages.
Prefer Debian packages or pip --only-binary=:all:; do not compile on this Pi.
Application: polarimeter.py. Simulation must remain explicit and labelled in
CSV records, plot titles and output. No automatic hardware fallback.
Keep plot history bounded, stream CSV to disk, and throttle plot redraws.
No optical calibration, wave-plate angle measurement or Stokes reconstruction
has been implemented; detector values are uncalibrated arbitrary units.

Initial setup session (2026-10-24) had restricted DNS/network, no usable sudo,
and no exposed USB serial devices. NumPy 2.2.4 and pyserial 3.5 import in .venv;
SciPy and Matplotlib were absent and their binary installation failed due to
DNS. Simulation CSV and parsers passed; plotting remains unverified until
those dependencies can be installed. See README for recovery/verification.
Group database lists cuqit in dialout, but session active groups omit dialout.
Do not claim hardware or GUI verification based on headless/simulated tests.

## Hardware context handoff (2026-11-26)

Before firmware, electrical, CAD, or motor-control work, read
[docs/hardware-integration-context.md](docs/hardware-integration-context.md).
It records the latest reported TB6612FNG/ESP32 wiring, encoder uncertainties,
Pi setup, mechanical dimensions, and validation requirements. It supplements
rather than certifies the initial setup above; reconcile against current source
and physical hardware. Do not energize or open ports during the initial audit.
Keep important engineering decisions and verification results in versioned docs
so ChatGPT and Codex can share context via the repository.
