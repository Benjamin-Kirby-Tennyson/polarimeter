# Rotating QWP polarimeter — hardware integration context

Date: 2026-11-26. Source: project conversation history, not a fresh physical inspection. This is a handoff from ChatGPT to Codex, not an as-built certification. The GitHub README and AGENTS.md currently describe the older initial setup (2026-10-24); local Pi work may be newer and not yet pushed. Reconcile with actual source and hardware before changing anything.

## Hardware inventory

- ESP32-D0WD-V3 development board, CP210x USB-UART bridge.
- TB6612FNG dual H-bridge, Channel A in use; replaces L298N.
- TSINY TS-25GA370H-75 brushed DC gearmotor, nominal 12 V, approximately 80 RPM, nominal 75:1 gearbox (verify manufacturer specification), six-wire integrated quadrature Hall encoder.
- External 12 V motor supply; solderless breadboard with exposed metal contacts.
- Raspberry Pi 4 Model B rev 1.5, 1 GB RAM, Debian 13 ARM64, Python 3.13; Waveshare 3.5-inch HDMI capacitive touchscreen.
- Rotating quarter-wave plate, geared optical holder and detector. Earlier handoff named PA100USB as optical acquisition instrument, but its identity/interface is unverified; inspect actual detector and acquisition implementation. Do not assume USB acquisition or ESP32 ADC wiring.

## Motor driver wiring

Latest documented project configuration (reported, not independently physically verified):

| TB6612FNG pin | Connection |
|---|---|
| VM | External motor supply +12 V |
| VCC | ESP32 3.3 V |
| GND | Common ground (ESP32, external supply negative, encoder ground) |
| STBY | 3.3 V |
| PWMA | ESP32 GPIO18 |
| AIN1 | ESP32 GPIO19 |
| AIN2 | ESP32 GPIO21 |
| AO1 | Motor M+ |
| AO2 | Motor M- |
| BIN1, BIN2, PWMB | Channel B unused; actual input termination unknown |
| BO1, BO2 | Channel B unused |

Historical/deprecated proposed pin map: PWMA GPIO25, AIN1 GPIO26, AIN2 GPIO27. Do not revert without physical/firmware evidence.

STBY is reported wired high, not under independent firmware control. Verify unused inputs are held in safe defined states. Verify driver supply limits, motor startup/stall current, thermal limits, local ceramic/bulk decoupling and motor transient handling before powering. Capacitor values and placement, breadboard row numbers, exact rail topology and other passive components are UNKNOWN. Do not feed 12 V into ESP32 logic, USB, encoder or GPIO. ESP32 normally powered through USB; keep motor power separate with common ground. Check ground return and supply current capacity.

## Encoder wiring and calibration

Six motor wires: M+, M-, encoder VCC, encoder GND, encoder A, encoder B. A prior handoff suggested GPIO32 (A), GPIO33 (B), 3.3 V encoder supply, and approximately 900 pulses per output revolution. These are unverified suggestions, NOT established wiring or calibration values. Wire colors are not reliably established. Confirm encoder pinout, allowable supply, output voltage/logic type, pullups/level translation, actual firmware pin definitions, and direction before connecting. ESP32 GPIO is not 5 V tolerant.

Determine motor-shaft pulses/rev, decoding mode (x1/x2/x4), gearbox output counts/rev, and actual external gear ratio. Do not multiply the gearbox ratio twice. If counts are calibrated at gearbox output, QWP counts/rev = gearbox-output counts/rev × driven gear teeth / pinion teeth. Document zero, sign, angle wrap/unwrap and timestamp conventions.

## Raspberry Pi, serial and acquisition

Reported later Pi setup: Tkinter GUI and touchscreen operational; NumPy, SciPy, Matplotlib and pyserial all passed imports in .venv. User cuqit was observed in dialout in later host output. CP210x enumerated at /dev/ttyUSB0 in kernel logs. The older restricted setup did not expose USB or GUI and lacked SciPy/Matplotlib; both records can be true. Verify current machine state rather than claiming new tests.

- Repo on Pi: ~/polarimeter; venv: ~/polarimeter/.venv (system site packages; lightweight dependencies).
- Previously used baud: 115200, not yet verified against current firmware. Prefer /dev/serial/by-id when available.
- Pi handles GUI, logging, plotting, synchronization and Stokes calculations; ESP32 handles real-time motor drive and encoder acquisition. This describes intended division of responsibility, not verified feature completion.
- Display: HDMI video plus USB touch/power reported; confirm physical connectors.
- Confirm detector instrument/model, signal path (including any photodiode/TIA/ADC), calibration, units, rate, timestamp and angular synchronization. Exact serial framing/commands and optical electrical wiring remain UNKNOWN until inspected.
- Preserve explicit simulation labels; no automatic fallback to hardware or simulation. Do not open ports just to discover them; opening may reset ESP32 despite DTR/RTS settings.

## Mechanical context — QWP V3.3 preliminary CAD

Motor horizontal, shaft horizontal; both gears vertical with parallel axes. Previously recommended 36-tooth pinion and 60-tooth QWP gear (external reduction 60:36 = 1.667:1); verify latest CAD and fabricated parts. Motor body and breadboard clip should minimize vibration, maintain optical axis/mesh alignment, reduce backlash and permit unobstructed aperture.

Measured motor dimensions (mm): exposed shaft 11.1; shaft diameter 4.1; gearbox OD 25.0; gearbox length 23.1; motor OD 24.3; motor length 31.1; encoder housing length 16.5; D-flat length 3.7. QWP holder dimensions remain provisional; verify physical part.

## Motor control objectives

PWM and direction, encoder position/velocity, closed-loop PID speed, ramping, safe stop/braking, stall detection, communication watchdog, startup/shutdown, continuous angle and zero calibration. Proposed 20 kHz PWM is not confirmed. Review firmware, actual PWM frequency/resolution and controller configuration rather than assuming implemented features.

## Historical experiment (reported, not revalidated)

Angle 800–3320 deg, commanded 25 deg/s, reported duration 150 s, 1211 samples. Normalized Stokes approximately [-0.259, -0.137, -0.971]; DoP 1.014, DoLP 0.293, azimuth -76 deg. DoP >1 is nonphysical and warrants calibration/uncertainty review; reconcile angle span, commanded speed and measured timestamps. Do not treat these as new or verified measurements.

## Codex handoff and audit

1. Read AGENTS.md, README.md, this file and current repository tree/history. Compare local Pi branch against GitHub without overwriting uncommitted changes.
2. Locate firmware and GPIO definitions, encoder decoding/calibration, PWM/PID, serial framing, acquisition and GUI. If firmware is absent, say so; request source or physical inspection rather than guessing.
3. Produce/update hardware inventory, pin-to-pin breadboard wiring, ESP32 pinout, motor driver integration, encoder calibration, system architecture and validation documentation using existing repository conventions. A single canonical source with links is preferable to duplicated pin maps.
4. Label each item DOCUMENTED (reported in project history), FIRMWARE VERIFIED, PHYSICALLY VERIFIED, PROPOSED, UNKNOWN or DEPRECATED. Software verification is not physical verification. Record evidence, date, and conflicts.
5. Include power/signal diagrams, safety risks, unresolved connections, physical verification checklist, test results and files changed. Keep existing working configuration until evidence supports a change.
6. Initial audit is read-only with respect to physical hardware: do not energize motor, open ports, send commands, flash firmware, invent breadboard contacts, assume encoder colors or change GPIOs. Use offline tests/simulation first. No hardware validation claim based on simulations.
7. Keep GitHub documentation current for future ChatGPT and Codex sessions; record significant decisions, assumptions and test outcomes in versioned project files.

## Electrical block diagram

```text
Raspberry Pi -- USB / CP210x --> ESP32
     |                            | GPIO18 PWM, GPIO19 AIN1, GPIO21 AIN2
     +-- HDMI/USB touchscreen     v
     +-- optical acquisition?   TB6612FNG Ch A <-- external 12 V (VM)
                                 | AO1/AO2
                                 v
                              TSINY motor --> gear train --> QWP
                                 |
                              Hall encoder A/B --> ESP32 (pins TBD)
ESP32 3.3 V --> driver VCC and STBY; common ground across motor/control circuits.
Optical detector interface and encoder supply/pins require verification.
```

## Immediate open items

Encoder A/B pins and supply; exact counts/rev and wire identification; passive components and breadboard rail/contact layout; motor current vs driver ratings; optical instrument and wiring; current firmware/protocol; latest gear tooth count; local-to-remote repository synchronization.
