"""Bounded, streamed SIMULATED Jones acquisition; no real detector interface.

Detector clock/truth are explicit fixtures. Clock mapping error can be checked
here because the simulator knows truth; a real instrument would need independent
clock characterization. Physical optical calibration remains unavailable.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
import hashlib
import math
import os
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

    def __init__(self, *, plan, exposure_us, period_us, detector_clock, clock_mapping, model=None, experiment_id=None, config_id=None, calibration_id=None):
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
        self.experiment_id = experiment_id or uuid4().hex
        self.config_id, self.calibration_id = config_id, calibration_id
        self.history = deque(maxlen=128)
        self.truth_history = deque(maxlen=128)
        self._identity = None
        self._discontinuity = None
        self.expected = self.missing = self.gap_count = 0
        self.pending_exposures = deque()
        self._next_end_us = self.period_us
        self._detector_sequence = 0
        self._detector_last_us = None
        self.drop_next_exposure = False
        self.cadence_source = "HOST_OBSERVED_SLOT_ESTIMATE"
        self.span_deg = self.contiguous_deg = 0.0
        self._first_valid = self._previous_valid = None
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

    def record_truth(self, time_us, angle):
        """Separate simulated physics timeline, independent of clock mapping."""
        uint(time_us)
        number(angle)
        if self.truth_history and time_us <= self.truth_history[-1]["time"]:
            if time_us == self.truth_history[-1]["time"]:
                return
            self.truth_history.clear()
        self.truth_history.append(dict(time=time_us, truth=angle))

    def observe(self, packet):
        if packet["mode"] != self.mode:
            raise ValueError("Hardware samples cannot enter a simulated acquisition")
        seq, now = int(packet["seq"]), int(packet["time_us"])
        current = tuple(packet[k] for k in ("boot_id", "session_id", "generation", "motion", "ref"))
        changed = self._identity is not None and current != self._identity
        backwards = self.history and now <= self.history[-1]["time"]
        if not changed and self._last_sequence is not None and seq == self._last_sequence:
            return None
        if changed or backwards or (self._last_sequence is not None and seq < self._last_sequence):
            self.history.clear()
            self._last_sequence = self._last_emit = None
            self._previous_valid = None
            self._discontinuity = "REFERENCE_OR_MOTION_CHANGED" if changed else "TIME_DISCONTINUITY"
        gap = self._last_sequence is not None and seq != self._last_sequence + 1
        self._last_sequence, self._identity = seq, current
        point = dict(time=now, sequence=seq, identity=current,
                     count=None if packet["count"] is None else int(packet["count"]),
                     angle=packet["angle"], truth=packet["truth"], velocity=packet["velocity"],
                     motion=packet["motion"], ref=packet["ref"], state=packet["state"], gap=gap)
        self.history.append(point)
        return point

    def record(self, packet):
        """Legacy offline fixture helper: HOST_OBSERVED_SLOT_ESTIMATE only.

        Production sessions use observe/advance/deliver with independent cadence.
        """
        point = self.observe(packet)
        if point is None:
            return None
        now = int(packet["time_us"])
        if packet["truth"] is not None and (not self.truth_history or self.truth_history[-1]["time"] < now):
            self.record_truth(now, packet["truth"])
        if packet["kind"] != "SCAN" or packet["state"] != "RUNNING" or point["angle"] is None:
            return None
        p = self.plan
        direction = 1 if p["plateau_end_deg"] > p["plateau_start_deg"] else -1
        if not 0 <= direction * (point["angle"] - p["plateau_start_deg"]) <= direction * (p["plateau_end_deg"] - p["plateau_start_deg"]):
            return None
        if self._last_emit is not None and now - self._last_emit < self.period_us:
            return None
        slots = 1 if self._last_emit is None else max(1, (now - self._last_emit) // self.period_us)
        self.expected += slots
        self.missing += slots - 1
        self._last_emit = now
        self.sequence += slots
        start, end = now - self.period_us - self.exposure_us, now - self.period_us
        sample = self.generate(start, end, self.sequence, packet)
        return self.associate(sample)

    def generate(self, start, end, sequence, packet):
        """Detector physics and identity at a scheduled exposure, before association."""
        optical_start = self.detector_clock.to_device(start)
        optical_end = self.detector_clock.to_device(end)
        sample = dict(mode=self.mode, experiment_id=self.experiment_id,
            boot_id=packet["boot_id"], session_id=packet["session_id"], arm_generation=packet["generation"],
            motion_id=packet["motion"], reference_epoch=packet["ref"], config_id=self.config_id,
            calibration_id=self.calibration_id, sequence=sequence, raw_sequence=None,
            actual_start_us=start, actual_end_us=end,
            optical_start_us=optical_start, optical_end_us=optical_end,
            mapped_start_us=None, mapped_end_us=None, midpoint_us=None,
            estimated_angle_deg=None, estimated_start_deg=None, estimated_end_deg=None,
            optical_truth_deg=None, truth_start_deg=None, truth_end_deg=None, intensity_au=None,
            estimated_velocity_deg_s=None, optical_truth_velocity_deg_s=None,
            encoder_low_us=None, encoder_high_us=None, count_low=None, count_high=None,
            bracket_sequences=None, bracket_identity=None,
            clock_error_us=None, timing_angle_bound_deg=None, quality="VALID")
        try:
            # Detector generation uses actual exposure endpoints, even when
            # association later fails. Preserve its raw measurement in CSV.
            truth_rows = list(self.truth_history)
            times = [start] + [r["time"] for r in truth_rows if start < r["time"] < end] + [end]
            values = [self.model.intensity(self.interpolate(truth_rows, t, "truth")[0]) for t in times]
            integral = sum((b-a)*(x+y)/2 for a,b,x,y in zip(times,times[1:],values,values[1:]))
            true_lo = self.interpolate(truth_rows, start, "truth")[0]
            true_hi = self.interpolate(truth_rows, end, "truth")[0]
            sample.update(intensity_au=integral/(end-start), truth_start_deg=true_lo, truth_end_deg=true_hi,
                optical_truth_deg=self.interpolate(truth_rows, (start+end)/2, "truth")[0],
                optical_truth_velocity_deg_s=(true_hi-true_lo)*1e6/(end-start))
        except (ValueError, ArithmeticError) as error:
            sample["quality"] = "DETECTOR_TRUTH_UNAVAILABLE: " + str(error)
        return sample

    def advance(self, now_us, physics):
        """Fixed 20ms fixture cadence; gate uses endpoint mechanical truth at end.

        Called independently of status RX. Collection requires SCAN/RUNNING and
        mechanical truth inside the directed plateau at exposure END. Full
        exposure/uncertainty acceptance is a separate host association decision.
        """
        uint(now_us)
        if self._detector_last_us is not None and now_us<self._detector_last_us:
            raise ValueError("Independent detector clock moved backwards")
        self._detector_last_us=now_us
        self.cadence_source = "INDEPENDENT_SIMULATED_DETECTOR"
        due = max(0, (now_us-self._next_end_us)//self.period_us+1)
        if due > 1:
            raise ValueError("Independent detector must be polled at least once per period")
        if not due:
            return
        end = self._next_end_us
        if end != now_us:
            raise ValueError("Fixture cadence requires exact exposure-end polling")
        self._next_end_us += self.period_us
        self._detector_sequence += 1
        p=self.plan
        direction=1 if p["plateau_end_deg"]>p["plateau_start_deg"] else -1
        angle=physics["mechanical_truth_deg"]
        gate=(physics["state"]=="RUNNING" and physics["kind"]=="SCAN" and
              0<=direction*(angle-p["plateau_start_deg"])<=direction*(p["plateau_end_deg"]-p["plateau_start_deg"]))
        if not gate:
            return
        self.expected += 1
        if self.drop_next_exposure:
            self.drop_next_exposure=False
            self.missing+=1
            return
        if len(self.pending_exposures)>=32:
            raise BufferError("Independent detector delivery queue overflow")
        sample=self.generate(end-self.exposure_us,end,self._detector_sequence,physics)
        self.pending_exposures.append((end+self.period_us,sample))

    def deliver(self, now_us, *, drain=False):
        uint(now_us)
        rows=[]
        while self.pending_exposures and (drain or self.pending_exposures[0][0]<=now_us):
            _,sample=self.pending_exposures.popleft()
            if drain:
                sample["quality"]="UNDELIVERED_AT_SESSION_END"
            rows.append(self.associate(sample))
        return rows

    def associate(self, sample):
        start,end=sample["actual_start_us"],sample["actual_end_us"]
        optical_start,optical_end=sample["optical_start_us"],sample["optical_end_us"]
        current=tuple(sample[k] for k in ("boot_id","session_id","arm_generation","motion_id","reference_epoch"))
        p=self.plan
        direction=1 if p["plateau_end_deg"]>p["plateau_start_deg"] else -1
        try:
            if sample["quality"]!="VALID":
                raise ValueError(sample["quality"])
            if self._discontinuity:
                reason, self._discontinuity = self._discontinuity, None
                raise ValueError(reason)
            if self.mapping is None:
                raise ValueError("CLOCK_UNMAPPED")
            lo, hi = self.mapping.to_host(optical_start), self.mapping.to_host(optical_end)
            if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
                raise ValueError("INVALID_MAPPED_SPAN")
            sample.update(mapped_start_us=lo, mapped_end_us=hi, midpoint_us=(lo+hi)/2)
            clock_error = max(abs(lo-start), abs(hi-end))
            sample["clock_error_us"] = clock_error
            if clock_error > self.mapping.uncertainty_us:
                raise ValueError("CLOCK_UNCERTAINTY")
            rows = list(self.history)
            estimated, left, right = self.interpolate(rows, (lo+hi)/2, "angle")
            est_lo = self.interpolate(rows, lo, "angle")[0]
            est_hi = self.interpolate(rows, hi, "angle")[0]
            # Accept only if the entire uncertainty-expanded extent is bracketed.
            u = self.mapping.uncertainty_us
            _, start_left, start_right = self.interpolate(rows, lo-u, "angle")
            _, end_left, end_right = self.interpolate(rows, hi+u, "angle")
            covered = [r for r in rows if lo-u <= r["time"] <= hi+u]
            covered.extend((left,right,start_left,start_right,end_left,end_right))
            ordered = sorted({r["time"]:r for r in covered}.values(), key=lambda r:r["time"])
            sample.update(raw_sequence=right["sequence"], bracket_sequences=json.dumps([r["sequence"] for r in ordered]),
                          bracket_identity=json.dumps(list(current)))
            if any(r["gap"] for r in covered):
                raise ValueError("SEQUENCE_GAP")
            if any(r["angle"] is None or not 0 <= direction*(r["angle"]-p["plateau_start_deg"])
                   <= direction*(p["plateau_end_deg"]-p["plateau_start_deg"]) for r in covered):
                raise ValueError("OUTSIDE_PLATEAU")
            if any(r["state"] != "RUNNING" or r["identity"] != current or r["ref"] is None for r in covered):
                raise ValueError("REFERENCE_OR_MOTION_CHANGED")
            if any(r["velocity"] is None or abs(r["velocity"]-direction*p["speed_deg_s"]) > p["plateau_speed_band_deg_s"] for r in covered):
                raise ValueError("NOT_STEADY_ROTATION")
            if any(direction*(b["angle"]-a["angle"]) < 0 for a,b in zip(ordered,ordered[1:])):
                raise ValueError("REVERSAL_IN_EXPOSURE")
            # Explicit configured speed ceiling bounds timing error throughout
            # the exposure; a point velocity is insufficient under acceleration.
            speed_bound = number(p.get("max_speed_deg_s", p["speed_deg_s"]+p["plateau_speed_band_deg_s"]))
            sample.update(estimated_angle_deg=estimated, estimated_start_deg=est_lo, estimated_end_deg=est_hi,
                encoder_low_us=left["time"], encoder_high_us=right["time"], count_low=left["count"], count_high=right["count"],
                estimated_velocity_deg_s=self.interpolate(rows,(lo+hi)/2,"velocity")[0],
                timing_angle_bound_deg=speed_bound*u/1e6)
            if any(isinstance(v,float) and not math.isfinite(v) for v in sample.values()):
                raise ValueError("NONFINITE_EXPOSURE")
        except (ValueError, ArithmeticError) as error:
            sample["quality"] = str(error)
            sample["estimated_angle_deg"] = sample["estimated_start_deg"] = sample["estimated_end_deg"] = None
        if sample["quality"] == "VALID":
            self.valid += 1
            if self._first_valid is None:
                self._first_valid = sample["estimated_angle_deg"]
            self.span_deg = abs(sample["estimated_angle_deg"]-self._first_valid)
            if (self._previous_valid is not None and self._previous_valid[0]+1 == sample["sequence"]
                    and self._previous_valid[2] == current):
                self.contiguous_deg += abs(sample["estimated_angle_deg"]-self._previous_valid[1])
            elif self._previous_valid is not None:
                self.gap_count += 1
            self._previous_valid = sample["sequence"], sample["estimated_angle_deg"], current
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
        self.raw_count = self.optical_count = 0
        self.raw_file = self.raw_path.open("x", newline="", encoding="utf-8")
        try:
            self.sample_file = self.sample_path.open("x", newline="", encoding="utf-8")
        except Exception:
            self.raw_file.close()
            raise
        try:
            self.raw = csv.DictWriter(self.raw_file, self.raw_fields, extrasaction="ignore")
            self.raw.writeheader()
        except Exception:
            self.close()
            raise
        self.optical = None
        self._last_flush = 0

    def write(self, packet, sample):
        if packet["mode"] != "SIMULATED":
            raise ValueError("Hardware record cannot enter simulation CSV")
        self.raw.writerow(packet)
        self.raw_count += 1
        if sample is not None:
            self.write_sample(sample)
        if int(packet["time_us"]) - self._last_flush >= 1000000:
            self.raw_file.flush()
            self.sample_file.flush()
            self._last_flush = int(packet["time_us"])

    def write_sample(self, sample):
        if sample["mode"]!="SIMULATED":
            raise ValueError("Only simulated optical records supported")
        if sample is not None:
            if self.optical is None:
                self.optical = csv.DictWriter(self.sample_file, tuple(sample))
                self.optical.writeheader()
            self.optical.writerow(sample)
            self.optical_count += 1

    def close(self):
        try:
            self.raw_file.close()
        finally:
            self.sample_file.close()

    def finish(self, *, phase, detail, metrics):
        if self.finished:
            return
        # Close/flush both data streams before publishing a final manifest.
        # A failed finalization must never advertise COMPLETED.
        error = None
        for stream in (self.raw_file, self.sample_file):
            try:
                stream.flush()
            except (OSError, ValueError) as exc:
                error = error or exc
            try:
                stream.close()
            except OSError as exc:
                error = error or exc
        if error is not None:
            phase, detail = "UNKNOWN", detail + "; final data flush/close failed: " + str(error)
        hashes={}
        if error is None:
            try:
                for label,path in (("encoder",self.raw_path),("optical",self.sample_path)):
                    digest=hashlib.sha256()
                    with path.open("rb") as stream:
                        for chunk in iter(lambda:stream.read(65536),b""):digest.update(chunk)
                    hashes[label]=digest.hexdigest()
            except OSError as exc:
                error=exc
                phase,detail="UNKNOWN",detail+"; final data hash failed: "+str(exc)
        record = dict(mode="SIMULATED", measured_hardware=False, reconstruction="PENDING",
                      phase=phase, detail=detail, data_complete=error is None,
                      metadata=self.metadata, metrics=metrics,
                      written_encoder_rows=self.raw_count, written_exposure_rows=self.optical_count,
                      files=dict(encoder=self.raw_path.name,optical=self.sample_path.name),data_sha256=hashes)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, allow_nan=False)
                stream.flush()
            os.replace(temporary, self.manifest_path)
            self.finished = True
        except OSError:
            # Leave .tmp as explicit partial evidence; never a final manifest.
            raise
        if error is not None:
            raise OSError(str(error))
