"""Dependency-free optics, simulated adapters, and streamed scan sessions.

No serial, USB, GPIO, or motor transport is imported or opened here.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import cmath
import csv
import math
from pathlib import Path
import time
from typing import Protocol
from uuid import uuid4


SIMULATED = "SIMULATED"
HARDWARE_LOCKED = "HARDWARE LOCKED"


def finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def target_angle(value):
    value = finite(value, "Target angle")
    if not 0 <= value <= 360:
        raise ValueError("Target angle must be between 0 and 360 degrees")
    return value


@dataclass(frozen=True)
class JonesModel:
    """Ideal lossless QWP followed by a fixed ideal linear analyzer.

    Lab x/y basis, fast axis u=(cos(theta), sin(theta)); slow axis
    v=(-sin(theta), cos(theta)). J = uu^T + exp(i*delta) vv^T.
    The input Jones vector is not normalized: its squared norm is incident
    intensity in arbitrary units. No noise, detector calibration or fitting.
    """

    ex: complex = 1 + 0j
    ey: complex = 0 + 0j
    analyzer_deg: float = 0.0
    retardance_rad: float = math.pi / 2

    def __post_init__(self):
        for field in (self.ex, self.ey):
            finite(field.real, "Jones amplitude")
            finite(field.imag, "Jones amplitude")
        if not math.isfinite(abs(self.ex)**2 + abs(self.ey)**2):
            raise ValueError("Incident intensity must be finite")
        finite(self.analyzer_deg, "Analyzer angle")
        finite(self.retardance_rad, "Retardance")

    def field(self, angle_deg):
        theta = math.radians(finite(angle_deg, "QWP angle") % 360)
        c, s = math.cos(theta), math.sin(theta)
        fast = c * self.ex + s * self.ey
        slow = (-s * self.ex + c * self.ey) * cmath.exp(1j * self.retardance_rad)
        return c * fast - s * slow, s * fast + c * slow

    def intensity(self, angle_deg):
        x, y = self.field(angle_deg)
        alpha = math.radians(self.analyzer_deg % 360)
        return abs(math.cos(alpha) * x + math.sin(alpha) * y)**2


class Motor(Protocol):
    """Future ESP32 adapter: verified angle feedback, move, stop.

    A real implementation needs inspected framing, homing, limits, timeout,
    angle provenance and an independent stop strategy before enabling it.
    """

    @property
    def angle_deg(self) -> float: ...
    def move_to(self, angle_deg: float) -> None: ...
    def stop(self) -> None: ...


class Detector(Protocol):
    """Future power-meter adapter must declare units, timing and calibration."""

    def read_intensity(self) -> float: ...


class SimulatedMotor:
    def __init__(self):
        self._angle = 0.0

    @property
    def angle_deg(self):
        return self._angle

    def move_to(self, angle_deg):
        self._angle = target_angle(angle_deg)

    def stop(self):
        pass


class SimulatedDetector:
    def __init__(self, motor, model):
        self.motor, self.model = motor, model

    def read_intensity(self):
        return self.model.intensity(self.motor.angle_deg)


class LockedMotor:
    @property
    def angle_deg(self):
        raise RuntimeError("Hardware angle feedback is unavailable")

    def move_to(self, angle_deg):
        raise RuntimeError("Physical control disabled: protocol and safety unverified")

    def stop(self):
        # This is intentionally a local no-op, never an unverified stop command.
        pass


class LockedDetector:
    def read_intensity(self):
        raise RuntimeError("Real detector acquisition is not configured")


class CsvLog:
    FIELDS = ("timestamp_utc", "elapsed_s", "mode", "qwp_angle_deg",
              "angle_source", "intensity", "intensity_unit", "detector_source",
              "model", "input_ex_real", "input_ex_imag", "input_ey_real",
              "input_ey_imag", "analyzer_deg", "retardance_rad")

    def __init__(self, directory, model):
        # This logger only accepts simulated records. A real acquisition logger
        # must have its own schema/provenance and separate hardware directory.
        folder = Path(directory) / "simulated"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = folder / f"simulated-qwp-{stamp}-{uuid4().hex[:8]}.csv"
        self.model = model
        self.file = self.path.open("x", newline="", encoding="utf-8")
        try:
            self.writer = csv.writer(self.file)
            self.writer.writerow(self.FIELDS)
            self.file.flush()
        except Exception:
            self.file.close()
            raise
        self.started = self.last_flush = time.monotonic()

    def write(self, angle, intensity):
        now = time.monotonic()
        m = self.model
        self.writer.writerow((datetime.now(timezone.utc).isoformat(),
                              f"{now - self.started:.6f}", SIMULATED, angle,
                              "SIMULATED_MODEL_ANGLE", intensity, "a.u. (uncalibrated)",
                              "SIMULATED_JONES", "ideal-qwp-fixed-analyzer-v1",
                              m.ex.real, m.ex.imag, m.ey.real, m.ey.imag,
                              m.analyzer_deg, m.retardance_rad))
        if now - self.last_flush >= 1:
            self.file.flush()
            self.last_flush = now

    def close(self):
        self.file.close()


class ScanController:
    """One finite 0..360 degree simulated scan, bounded history, streamed CSV.

    Tick is called by Tk's event loop. Slow ticks do not queue catch-up moves.
    Manual readings share a file until Stop; each scan starts a fresh file.
    """

    def __init__(self, *, simulate=False, directory="data", window=361, model=None):
        if not 2 <= window <= 2000:
            raise ValueError("GUI history must contain 2..2000 points")
        self.mode = SIMULATED if simulate else HARDWARE_LOCKED
        self.model = model or JonesModel()
        self.motor = SimulatedMotor() if simulate else LockedMotor()
        self.detector = (SimulatedDetector(self.motor, self.model) if simulate
                         else LockedDetector())
        self.directory = directory
        self.history = deque(maxlen=window)
        self.running = False
        self.log = None
        self.last_path = None
        self.next_angle = 0.0

    def _require_simulation(self):
        if self.mode != SIMULATED:
            raise RuntimeError("Physical control disabled: protocol and safety unverified")

    def _open_log(self):
        if self.log is None:
            self.log = CsvLog(self.directory, self.model)
            self.last_path = self.log.path

    def start(self):
        self._require_simulation()
        self.stop()
        self._open_log()  # Fail before starting if storage is unavailable.
        self.history.clear()
        self.next_angle = 0.0
        self.running = True

    def _record(self, angle):
        self._require_simulation()
        try:
            self._open_log()
            self.motor.move_to(angle)
            value = finite(self.detector.read_intensity(), "Intensity")
            self.log.write(self.motor.angle_deg, value)
            point = self.motor.angle_deg, value
            self.history.append(point)
            return point
        except Exception:
            self.stop()
            raise

    def tick(self):
        if not self.running:
            return None
        point = self._record(self.next_angle)
        if self.next_angle >= 360:
            self.stop()
        else:
            self.next_angle += 2.0
        return point

    def move_to(self, angle):
        self._require_simulation()
        if self.running:
            raise RuntimeError("Stop the scan before changing the simulated angle")
        return self._record(target_angle(angle))

    def jog(self, delta):
        self._require_simulation()
        return self.move_to((self.motor.angle_deg + finite(delta, "Jog")) % 360)

    def stop(self):
        self.running = False
        try:
            self.motor.stop()
        finally:
            log, self.log = self.log, None
            if log is not None:
                log.close()
