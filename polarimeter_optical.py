"""Bounded, streamed SIMULATED Jones acquisition; no real detector interface.

Detector clock/truth are explicit fixtures. Clock mapping error can be checked
here because the simulator knows truth; a real instrument would need independent
clock characterization. No Stokes reconstruction or optical calibration exists.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from uuid import uuid4

from polarimeter_core import JonesModel
from polarimeter_controller import number, uint


@dataclass(frozen=True)
class OpticalClock:
    offset_us: float
    drift_ppm: float
    uncertainty_us: float

    def __post_init__(self):
        number(self.offset_us)
        if number(self.drift_ppm) <= -1e6 or number(self.uncertainty_us) < 0:
            raise ValueError("Invalid simulated clock mapping")

    def to_device(self, host_us):
        return round(host_us * (1 + self.drift_ppm / 1e6) + self.offset_us)

    def to_host(self, device_us):
        return (device_us - self.offset_us) / (1 + self.drift_ppm / 1e6)


class OpticalAcquisition:
    mode = "SIMULATED"

    def __init__(self, *, plan, exposure_us, period_us, detector_clock, clock_mapping, model=None):
        uint(exposure_us, positive=True)
        uint(period_us, positive=True)
        if exposure_us > period_us or exposure_us > 500000:
            raise ValueError("Bounded exposure shorter than sample period required")
        if type(detector_clock) is not OpticalClock or (clock_mapping is not None and type(clock_mapping) is not OpticalClock):
            raise TypeError("Explicit simulated clock models required")
        self.plan = dict(plan)
        self.exposure_us, self.period_us = exposure_us, period_us
        self.detector_clock, self.mapping = detector_clock, clock_mapping
        self.model = model if model is not None else JonesModel()
        self.history = deque(maxlen=128)
        self._last_sequence = self._last_emit = None
        self.sequence = self.valid = self.rejected = 0

    @staticmethod
    def interpolate(rows, time_us, field):
        for left, right in zip(rows, rows[1:]):
            if left["time"] <= time_us <= right["time"] and right["time"] > left["time"]:
                a, b = left[field], right[field]
                if a is None or b is None:
                    raise ValueError("Missing bracketing values")
                fraction = (time_us - left["time"]) / (right["time"] - left["time"])
                return a + fraction * (b - a), left, right
        raise ValueError("Exposure is not bracketed by encoder samples")

    def record(self, packet):
        if packet["mode"] != self.mode:
            raise ValueError("Hardware samples cannot enter a simulated acquisition")
        seq, now = int(packet["seq"]), int(packet["time_us"])
        if self._last_sequence is not None and seq <= self._last_sequence:
            return None
        gap = self._last_sequence is not None and seq != self._last_sequence + 1
        self._last_sequence = seq
        point = dict(time=now, count=None if packet["count"] is None else int(packet["count"]),
                     angle=packet["angle"], truth=packet["truth"], velocity=packet["velocity"],
                     motion=packet["motion"], ref=packet["ref"], state=packet["state"], gap=gap)
        self.history.append(point)
        if packet["kind"] != "SCAN" or packet["state"] != "RUNNING" or point["angle"] is None:
            return None
        p = self.plan
        direction = 1 if p["plateau_end_deg"] > p["plateau_start_deg"] else -1
        if not 0 <= direction * (point["angle"] - p["plateau_start_deg"]) <= direction * (p["plateau_end_deg"] - p["plateau_start_deg"]):
            return None
        if self._last_emit is not None and now - self._last_emit < self.period_us:
            return None
        self._last_emit = now
        self.sequence += 1
        # One explicit sample-period delivery latency leaves encoder brackets
        # available on both sides even with timestamp-rounding uncertainty.
        start, end = now - self.period_us - self.exposure_us, now - self.period_us
        optical_start = self.detector_clock.to_device(start)
        optical_end = self.detector_clock.to_device(end)
        sample = dict(mode=self.mode, sequence=self.sequence, motion_id=point["motion"],
            optical_start_us=optical_start, optical_end_us=optical_end,
            mapped_start_us=None, mapped_end_us=None, midpoint_us=None,
            estimated_angle_deg=None, optical_truth_deg=None, intensity_au=None,
            estimated_velocity_deg_s=None, optical_truth_velocity_deg_s=None,
            encoder_low_us=None, encoder_high_us=None, count_low=None, count_high=None,
            clock_error_us=None, timing_angle_bound_deg=None, quality="VALID")
        try:
            if self.mapping is None:
                raise ValueError("CLOCK_UNMAPPED")
            lo, hi = self.mapping.to_host(optical_start), self.mapping.to_host(optical_end)
            sample.update(mapped_start_us=lo, mapped_end_us=hi, midpoint_us=(lo + hi) / 2)
            clock_error = max(abs(lo - start), abs(hi - end))
            sample["clock_error_us"] = clock_error
            if clock_error > self.mapping.uncertainty_us:
                raise ValueError("CLOCK_UNCERTAINTY")
            rows = list(self.history)
            estimated, left, right = self.interpolate(rows, (lo + hi) / 2, "angle")
            _, start_left, start_right = self.interpolate(rows, lo, "angle")
            _, end_left, end_right = self.interpolate(rows, hi, "angle")
            covered = [r for r in rows if lo <= r["time"] <= hi]
            covered.extend((left, right, start_left, start_right, end_left, end_right))
            if any(r["gap"] for r in covered):
                raise ValueError("SEQUENCE_GAP")
            if any(r["angle"] is None or not 0 <= direction * (r["angle"] - p["plateau_start_deg"])
                   <= direction * (p["plateau_end_deg"] - p["plateau_start_deg"]) for r in covered):
                raise ValueError("OUTSIDE_PLATEAU")
            if any(r["state"] != "RUNNING" or r["motion"] != point["motion"] or r["ref"] != point["ref"] or r["ref"] is None for r in covered):
                raise ValueError("REFERENCE_OR_MOTION_CHANGED")
            if any(r["velocity"] is None or abs(r["velocity"] - direction * p["speed_deg_s"]) > p["plateau_speed_band_deg_s"] for r in covered):
                raise ValueError("NOT_STEADY_ROTATION")
            ordered = sorted({r["time"]: r for r in covered}.values(), key=lambda r: r["time"])
            if any(direction * (b["angle"] - a["angle"]) < 0 for a, b in zip(ordered, ordered[1:])):
                raise ValueError("REVERSAL_IN_EXPOSURE")
            truth, _, _ = self.interpolate(rows, (lo + hi) / 2, "truth")
            estimated_velocity, _, _ = self.interpolate(rows, (lo + hi) / 2, "velocity")
            true_lo = self.interpolate(rows, lo, "truth")[0]
            true_hi = self.interpolate(rows, hi, "truth")[0]
            times = [lo] + [r["time"] for r in rows if lo < r["time"] < hi] + [hi]
            values = [self.model.intensity(self.interpolate(rows, t, "truth")[0]) for t in times]
            integral = sum((b - a) * (x + y) / 2 for a, b, x, y in zip(times, times[1:], values, values[1:]))
            sample.update(estimated_angle_deg=estimated, optical_truth_deg=truth,
                intensity_au=integral / (hi - lo), encoder_low_us=left["time"], encoder_high_us=right["time"],
                count_low=left["count"], count_high=right["count"],
                estimated_velocity_deg_s=estimated_velocity,
                optical_truth_velocity_deg_s=(true_hi - true_lo) * 1e6 / (hi - lo),
                timing_angle_bound_deg=abs(point["velocity"]) * self.mapping.uncertainty_us / 1e6)
        except ValueError as error:
            sample["quality"] = str(error)
        if sample["quality"] == "VALID":
            self.valid += 1
        else:
            self.rejected += 1
        return sample


class RotationLog:
    """Stream raw and exposure records into separate explicit simulation files."""
    raw_fields = ("mode", "seq", "time_us", "host_received_us", "boot_id", "session_id", "motion", "kind", "state",
                  "generation", "ref", "count", "angle", "velocity", "truth", "duty")

    def __init__(self, directory, *, metadata=None):
        folder = Path(directory) / "simulated-rotation"
        folder.mkdir(parents=True, exist_ok=True)
        name = "simulated-rotation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
        self.raw_path, self.sample_path = folder / (name + "-encoder.csv"), folder / (name + "-optical.csv")
        self.manifest_path = folder / (name + "-session.json")
        self.metadata = dict(metadata or {})
        self.finished = False
        self.raw_file = self.raw_path.open("x", newline="", encoding="utf-8")
        try:
            self.sample_file = self.sample_path.open("x", newline="", encoding="utf-8")
        except Exception:
            self.raw_file.close()
            raise
        self.raw = csv.DictWriter(self.raw_file, self.raw_fields, extrasaction="ignore")
        self.raw.writeheader()
        self.optical = None
        self._last_flush = 0

    def write(self, packet, sample):
        if packet["mode"] != "SIMULATED":
            raise ValueError("Hardware record cannot enter simulation CSV")
        self.raw.writerow(packet)
        if sample is not None:
            if self.optical is None:
                self.optical = csv.DictWriter(self.sample_file, tuple(sample))
                self.optical.writeheader()
            self.optical.writerow(sample)
        if int(packet["time_us"]) - self._last_flush >= 1000000:
            self.raw_file.flush()
            self.sample_file.flush()
            self._last_flush = int(packet["time_us"])

    def close(self):
        try:
            self.raw_file.close()
        finally:
            self.sample_file.close()

    def finish(self, *, phase, detail, metrics):
        if not self.finished:
            with self.manifest_path.open("x", encoding="utf-8") as stream:
                json.dump(dict(mode="SIMULATED", measured_hardware=False, reconstruction="PENDING",
                               phase=phase, detail=detail, metadata=self.metadata, metrics=metrics), stream, indent=2)
            self.finished = True
        self.close()
