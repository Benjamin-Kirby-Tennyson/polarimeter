# Physical commissioning evidence checklist

2026-11-27. **NOT PHYSICALLY VERIFIED.** This checklist records required evidence;
it does not authorize powering, opening ports, flashing or moving the motor.
The [canonical reported hardware](hardware-integration-context.md) remains the
as-reported source. Compiled mock firmware is not the installed firmware.

## A. User evidence with all power disconnected

Disconnect motor energy and USB/other possible back-power sources before wiring
inspection or continuity checks. Confirm isolation rather than assuming a stopped
GUI means an isolated motor. Do not change pins or supply an unidentified encoder.
Photographs/labels, saved files, manufacturer documents and unpowered continuity
records can be collected without running this app against hardware.

| Priority / evidence | Current record | Required evidence and instrument |
| --- | --- | --- |
| 1. Actual ESP32 development board | ESP32-D0WD-V3 reported; exact board/flash UNKNOWN | Clear front/back board and module labels; board manufacturer/model/revision and documented flash/boot settings. Camera and board documentation; no board interrogation needed. |
| 2. Original Arduino source on Windows | Historical COM5 upload; original sketch UNKNOWN | In Arduino IDE inspect recent sketches and configured sketchbook directory; copy the complete sketch folder with local headers/libraries/build manifest. Record file paths, dates, IDE/core version and any saved upload log. Do not upload or open Serial Monitor. Preserve originals and hash the copy. |
| 3. Encoder wire/header mapping | Six-wire motor/encoder reported; colors/pinout UNKNOWN | Identify M+/M−, encoder power/ground/A/B from exact motor/encoder documentation and labelled connectors. Photograph both ends; use unpowered continuity only where appropriate. Encoder allowable supply, output type/voltage, pullups/level translation and actual ESP32 A/B pins remain UNKNOWN. Do not adopt earlier GPIO32/33 or 3.3 V suggestions. |
| 4. Driver point-to-point wiring | TB6612FNG A, GPIO18/19/21 and STBY tied high reported | Record actual driver/carrier model and every named net to endpoint, including VM/VCC/GND, STBY, PWMA/AIN1/AIN2, AO1/AO2 and unused B inputs. Include rail breaks, decoupling and exposed breadboard contacts. Camera, unpowered continuity and carrier schematic. Use the canonical pin map as a report to check, not proof. |
| 5. Motor/driver/supply suitability | TSINY TS-25GA370H-75, nominal 12 V/80 RPM reported; current suitability UNKNOWN | Exact motor label/datasheet, rated/startup/stall current and load/gearbox details; actual TB6612FNG package/carrier current/thermal/voltage limits; supply label, current limiting and wiring/connector ratings. Compare manufacturer limits with margin. Do not deliberately stall the motor to discover current. |
| 6. Independent default-inhibited disconnect | STBY reported tied high; independent energy disable UNKNOWN | Proposed schematic and identifiable physical motor-energy isolation/disconnect. Document how power is removed on boot/reset/unplug/host crash, how the user can independently stop energy, and how isolation will be verified before a later test. Firmware GPIO/STOP alone is insufficient evidence. |
| 7. Mechanical transmission and reference | 36:60 teeth proposed; actual teeth/zero/backlash UNKNOWN | Count actual pinion/driven teeth, record shaft coupling and QWP axis relationship, travel limits/guards and alignment. Record method/instrument for mechanical and optical fast-axis zero separately. Do not force the mechanism or invent an optical zero from encoder count. |
| 8. Detector/acquisition chain | Photodiode named; PA100USB historical reference unverified | Actual detector/instrument model/label; photodiode/TIA/ADC chain if present; connector/interface documentation, calibration/units/range, sampling/exposure timing and timestamp source. Photographs and manufacturer documents; no live interface probing. |

Use the [Toshiba TB6612FNG datasheet](https://toshiba.semicon-storage.com/info/TB6612FNG_datasheet_en_20141001.pdf?did=10660&prodName=TB6612FNG)
for operating ratings, pulse-current conditions, bridge/standby truth table and
thermal/package conditions. Peak pulse current is not a continuous carrier
rating. Use the [ESP32 Series Datasheet](https://documentation.espressif.com/esp32_datasheet_en.html)
for the identified chip's electrical and boot constraints. These sources do not
identify the attached carrier, encoder or development-board wiring.

Store a dated, sanitized evidence record under version control with source links,
measured units, instrument model/range and who observed it. Keep each field
REPORTED, DOCUMENT-VERIFIED, PHYSICALLY VERIFIED, PROPOSED or UNKNOWN. Do not turn
an earlier suggestion or a simulation constant into a verified measurement.
Original project sources can contain local credentials; inspect before sharing.

## B. Separate later logic/encoder commissioning gate

This stage requires a reviewed wiring/electrical plan, **verified motor energy
isolation**, and separate explicit physical authorization. It is not performed
by this software session. Appropriate DMM/oscilloscope or logic analyzer and an
identified, current-limited supply may be needed. Establish allowable encoder
supply and A/B output levels before connecting them to ESP32 GPIO. Confirm common
reference, polarity, decoding mode, continuity/reset behavior, and actual counts
per gearbox-output turn by a documented procedure. Then apply the measured
external tooth ratio exactly once. Do not assume CPR, voltage, colors or GPIOs.

Any USB connection/port opening may reset the board. Installed source/binary,
boot/reset behavior and passive-observation plan must be reviewed first. No baud
or framing is inferred from the proposed protocol or historical examples. The
mock PCNT/UART/LEDC adapters remain unavailable and cannot be enabled by a flag.

## C. Separate later powered motor/optical gate

Requires acceptance of A/B evidence and separately authorized powered testing.
Review current/thermal/transient suitability, independent default-inhibited
energy interruption, actual firmware identity/build/configuration and measured
encoder/reference validity before introducing motor energy. Plan bounded,
current-limited movement, observation, stop/lease/reset/disconnect fault tests,
containment/coast distance and thermal limits with appropriate instruments.
Do not equate a command acknowledgment or inhibited output with physical stopping.

Optical acquisition then needs a confirmed instrument/interface, calibration,
fast-axis/analyzer conventions and real clock synchronization/error budget.
Only after these gates can physical trajectory/PI tuning and optical accuracy
be evaluated. No gains, motor duty, PWM frequency, limits or motion duration are
provided as physical defaults by this checklist.

The first useful handoff is items **1–6** above: board labels, saved original
sketch, documented encoder wiring/levels, actual driver wiring/ratings and the
independent energy-isolation design. Software can continue offline while those
unknowns are recorded; no physical action is performed automatically.
