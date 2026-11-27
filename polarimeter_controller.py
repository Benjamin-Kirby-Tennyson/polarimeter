"""Simulation-only protocol state model; no serial, GPIO or physical adapter.

This is a proposed replacement-protocol reference, not installed firmware.
Trajectory/PI/plant models are separate in polarimeter_simulation.py. This
module derives feedback from raw counts; it does not decode physical A/B pins.
All clock samples, feedback and output state are explicitly simulated.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from itertools import islice
import math
import re
import secrets

from polarimeter_protocol import FrameError, FrameMessage


U64 = 2**64 - 1
MAX_EVENTS = 8


def uint(value, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= U64):
        raise ValueError("Require an unsigned 64-bit integer")
    return value


def decimal(value, *, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", value):
        raise ValueError("Require a canonical unsigned decimal string")
    return uint(int(value), positive=positive)


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Require a 128-bit lowercase hexadecimal identity")
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Require a finite number, not a boolean")
    return value


@dataclass(frozen=True)
class Timing:
    lease_us: int
    request_us: int
    feedback_us: int
    cycle_us: int
    motion_us: int

    def __post_init__(self):
        for value in vars(self).values():
            uint(value, positive=True)


@dataclass(frozen=True)
class Readiness:
    stationary_band_deg_s: float
    stationary_dwell_us: int
    max_count_rate_s: float
    velocity_window_us: int = 0  # Zero means two-sample estimator in this offline model.

    def __post_init__(self):
        if number(self.stationary_band_deg_s) < 0 or number(self.max_count_rate_s) <= 0:
            raise ValueError("Invalid simulated readiness limits")
        uint(self.stationary_dwell_us)
        uint(self.velocity_window_us)


@dataclass(frozen=True)
class EncoderSample:
    """Raw simulated encoder input; no independently asserted angle/velocity."""
    sample_us: int
    count: int | None
    continuity_id: str
    valid: bool = True

    def __post_init__(self):
        uint(self.sample_us)
        identity(self.continuity_id)
        if type(self.valid) is not bool:
            raise ValueError("Encoder validity must be boolean")
        if self.count is not None and (type(self.count) is not int or not -(2**63) <= self.count < 2**63):
            raise ValueError("Invalid signed count")
        if self.valid and self.count is None:
            raise ValueError("Valid sample requires raw count")


@dataclass(frozen=True)
class SimulatedReference:
    epoch: str
    calibration_id: str
    count_zero: int
    qwp_zero_deg: float
    direction: int

    def __post_init__(self):
        identity(self.epoch)
        identity(self.calibration_id)
        number(self.qwp_zero_deg)
        if type(self.count_zero) is not int or not -(2**63) <= self.count_zero < 2**63:
            raise ValueError("Invalid reference count")
        if type(self.direction) is not int or self.direction not in (-1, 1):
            raise ValueError("Direction must be +1 or -1")


@dataclass(frozen=True)
class SimulatedFeedback:
    sample_us: int
    count: int | None = None
    angle_deg: float | None = None
    velocity_deg_s: float | None = None
    reference_valid: bool = False
    calibration_id: str | None = None

    def __post_init__(self):
        uint(self.sample_us)
        if self.count is not None and (type(self.count) is not int or not -(2**63) <= self.count < 2**63):
            raise ValueError("Count must be a signed 64-bit integer or unknown")
        for value in (self.angle_deg, self.velocity_deg_s):
            if value is not None:
                number(value)
        if type(self.reference_valid) is not bool:
            raise ValueError("Reference validity must be boolean")
        if self.calibration_id is not None:
            identity(self.calibration_id)
        if self.reference_valid and (self.count is None or self.angle_deg is None
                                     or self.calibration_id is None):
            raise ValueError("Valid reference requires count, angle and calibration identity")


@dataclass
class SimulatedOutput:
    """In-memory state only; no callback, driver pin, PWM value or I/O."""
    active: bool = False
    inhibit_count: int = 0
    enable_count: int = 0
    duty: float = 0.0

    def inhibit(self):
        self.active = False
        self.duty = 0.0
        self.inhibit_count = min(U64, self.inhibit_count + 1)

    def enable(self):
        self.active = True
        self.enable_count = min(U64, self.enable_count + 1)

    def set_duty(self, duty):
        number(duty)
        if not -1 <= duty <= 1:
            raise ValueError("Signed simulated duty outside [-1,1]")
        self.duty = duty if self.active else 0.0


class OfflineController:
    mode = "SIMULATED"
    _base = {"v", "mode", "type", "request_id", "boot_id", "session_id"}
    _extra = {
        "STOP": set(), "HELLO": {"host_nonce"}, "GET_STATUS": set(),
        "CONFIGURE": {"not_after_us", "config_generation", "config"},
        "ARM": {"not_after_us", "config_id", "challenge"},
        "HEARTBEAT": {"not_after_us", "arm_generation", "heartbeat_seq"},
        "MOVE_TO": {"not_after_us", "arm_generation", "target_deg", "speed_deg_s",
                    "acceleration_deg_s2", "duration_us"},
        "SCAN": {"not_after_us", "arm_generation", "end_deg", "speed_deg_s",
                 "acceleration_deg_s2", "duration_us", "plateau_start_deg",
                 "plateau_end_deg", "plateau_speed_band_deg_s"},
        "DISARM": {"not_after_us"}, "CLEAR_FAULT": {"not_after_us"},
    }

    def __init__(self, *, timing, readiness, boot_id, output=None, id_factory=None):
        if not isinstance(timing, Timing):
            raise TypeError("Explicit simulated Timing required")
        if type(readiness) is not Readiness:
            raise TypeError("Explicit simulation readiness policy required")
        self.readiness = readiness
        if output is not None and type(output) is not SimulatedOutput:
            raise TypeError("Only the in-memory SimulatedOutput is supported")
        self.timing, self.boot_id = timing, identity(boot_id)
        self.output = output if output is not None else SimulatedOutput()
        self.id_factory = id_factory or (lambda: secrets.token_hex(16))
        self._identities = deque([self.boot_id], maxlen=32)
        self._cache = OrderedDict()
        self.state = "DISARMED"
        self.faults = set()
        self.session_id = self.config = self.feedback = None
        self.config_generation = self.arm_generation = self._request_id = self._heartbeat_seq = 0
        self.lease_end = self.motion_end = self.challenge = None
        self._now = self._last_time = None
        self._heartbeat_payload = None
        self.target_deg = None
        self.motion_limits = self._settled_since = None
        self.motion_request_id = self.motion_kind = self.scan_plan = None
        self._reference = self._encoder = self._stationary_since = None
        self._reference_epochs = deque(maxlen=32)
        self._encoder_window = deque(maxlen=128)
        self._session_tainted = self._session_exhausted = False
        self._clean_protocol = True
        self._admission_ok = self._cycle_healthy = True
        self._external_inhibit = False
        self._stall_release_ack = False
        self.output.inhibit()

    def _new_identity(self):
        try:
            result = identity(self.id_factory())
            if result in self._identities:
                raise ValueError("Identity source repeated a recent identity")
        except Exception as exc:
            self._fault("IDENTITY")
            raise ValueError("Identity source unavailable or reused") from exc
        self._identities.append(result)
        return result

    def _inhibit(self):
        self.output.inhibit()
        self.lease_end = self.motion_end = self.challenge = None
        self.target_deg = None
        self.motion_limits = self._settled_since = None
        self.motion_request_id = self.motion_kind = self.scan_plan = None
        self._heartbeat_seq = 0
        self._heartbeat_payload = None
        if self.arm_generation == U64:
            self.faults.add("COUNTER_OVERFLOW")
        else:
            self.arm_generation += 1

    def _fault(self, cause):
        # Finite vocabulary and capped set: no untrusted fault strings retained.
        self.faults.add(cause)
        if cause == "STALL":
            self._stall_release_ack = False
        if cause in ("FEEDBACK", "FEEDBACK_INTEGRITY", "CLOCK", "TIME_OVERFLOW"):
            self._invalidate_reference()
            self._stationary_since = None
        if cause == "ID_CONFLICT":
            self._session_tainted = True
        if cause == "SEQUENCE_EXHAUSTED":
            self._session_exhausted = True
        if cause in ("PROTOCOL", "RX_OVERFLOW", "TX_OVERFLOW"):
            self._clean_protocol = False
        if cause in ("CLOCK", "TIME_OVERFLOW"):
            self.feedback = None
        self._inhibit()
        self.state = "FAULT"

    def _stop(self):
        self._inhibit()
        self.state = "FAULT" if self.faults else "STOPPED"

    def _sum(self, a, b):
        if a > U64 - b:
            raise ValueError("Device-time horizon overflows uint64")
        return a + b

    def _deadline(self, value, horizon):
        deadline = decimal(value, positive=True)
        if not self._now < deadline <= self._sum(self._now, horizon):
            raise ValueError("Expired or excessively future deadline")
        return deadline

    def _fresh(self, *, reference=False):
        f = self.feedback
        return (f is not None and f.sample_us <= self._now
                and self._now - f.sample_us < self.timing.feedback_us
                and f.count is not None and f.velocity_deg_s is not None
                and (not reference or (f.reference_valid and self.config is not None
                     and f.calibration_id == self.config["calibration_id"])))

    def _invalidate_reference(self):
        self._reference = self.challenge = None
        if self.feedback is not None:
            self.feedback = replace(self.feedback, angle_deg=None, reference_valid=False,
                                    calibration_id=None)

    def _inside(self):
        return (self._fresh(reference=True)
                and self.config["min_angle_deg"] <= self.feedback.angle_deg <= self.config["max_angle_deg"])

    def _stationary(self):
        return (self._fresh() and self._stationary_since is not None
                and abs(self.feedback.velocity_deg_s) <= self.readiness.stationary_band_deg_s
                and self.feedback.sample_us - self._stationary_since >= self.readiness.stationary_dwell_us)

    def _derive(self, sample, velocity):
        ref = self._reference
        good = (sample.valid and ref is not None and self.config is not None
                and ref.calibration_id == self.config["calibration_id"])
        angle = (ref.qwp_zero_deg + ref.direction * (sample.count - ref.count_zero)
                 * 360 / self.config["counts_per_qwp_rev"]) if good else None
        self.feedback = SimulatedFeedback(sample.sample_us, sample.count, angle,
                                         velocity, good, ref.calibration_id if good else None)
        if velocity is not None and abs(velocity) <= self.readiness.stationary_band_deg_s:
            if self._stationary_since is None:
                self._stationary_since = sample.sample_us
        else:
            self._stationary_since = None

    def _ingest(self, sample):
        if type(sample) is not EncoderSample or sample.sample_us > self._now:
            self._encoder_window.clear()
            self._encoder = None
            self.feedback = None
            self._stationary_since = None
            return "FEEDBACK_INTEGRITY"
        old = self._encoder
        if old is not None and (sample.sample_us < old.sample_us
                                or (sample.sample_us == old.sample_us and sample != old)):
            self._encoder = None
            self._encoder_window.clear()
            self.feedback = None
            self._stationary_since = None
            return "FEEDBACK_INTEGRITY"
        if sample == old:
            return None
        velocity = None
        cause = None
        if not sample.valid:
            self._encoder_window.clear()
            self._invalidate_reference()
            cause = "FEEDBACK"
        elif old is not None and old.valid:
            if sample.continuity_id != old.continuity_id:
                self._encoder_window.clear()
                self._invalidate_reference()
                self._stationary_since = None
                cause = "FEEDBACK_INTEGRITY"
            elif sample.sample_us - old.sample_us >= self.timing.feedback_us:
                # A newly fresh sample cannot erase an unobserved encoder gap.
                self._encoder_window.clear()
                self._invalidate_reference()
                self._stationary_since = None
                cause = "FEEDBACK"
            else:
                dt = (sample.sample_us - old.sample_us) / 1e6
                delta = sample.count - old.count
                if abs(delta) > self.readiness.max_count_rate_s * dt + 1:
                    cause = "FEEDBACK_INTEGRITY"
                    self._invalidate_reference()
                elif self.config is not None:
                    sign = self._reference.direction if self._reference is not None else 1
                    base = old
                    if self.readiness.velocity_window_us:
                        base = next((p for p in self._encoder_window
                                     if sample.sample_us - self.readiness.velocity_window_us <= p.sample_us
                                     < sample.sample_us), old)
                    window_s = (sample.sample_us - base.sample_us) / 1e6
                    velocity = sign * (sample.count - base.count) * 360 / (self.config["counts_per_qwp_rev"] * window_s)
                elif delta == 0:
                    velocity = 0.0  # Stationary raw-count evidence, not angular calibration.
        self._encoder = sample
        if sample.valid:
            self._encoder_window.append(sample)
        self._derive(sample, velocity)
        return cause

    def establish_reference(self, reference):
        """Explicit power-free simulated reference, never physical commissioning."""
        if (type(reference) is not SimulatedReference or self.output.active
                or self.state not in ("DISARMED", "CONFIGURED", "STOPPED", "FAULT")
                or self.config is None or not self._stationary()
                or reference.calibration_id != self.config["calibration_id"]
                or reference.epoch in self._reference_epochs):
            raise ValueError("Reference requires new epoch and inhibited stationary simulation")
        if self.faults & {"TRAVEL_LIMIT", "TRAVEL_PREDICTED"}:
            raise ValueError("Re-zeroing cannot clear a travel concern; retain the mechanical frame")
        number(reference.qwp_zero_deg + reference.direction * (self._encoder.count - reference.count_zero)
               * 360 / self.config["counts_per_qwp_rev"])  # Validate arithmetic before changing reference.
        self._reference_epochs.append(reference.epoch)
        self._reference, self.challenge = reference, None
        self._derive(self._encoder, self.feedback.velocity_deg_s)

    def _schema(self, event):
        if type(event) is not FrameMessage:
            raise ValueError("Require a validated immutable frame event")
        m = event.message
        kind = m.get("type")
        if not isinstance(kind, str) or kind not in self._extra:
            raise ValueError("Unknown message type")
        if set(m) != self._base | self._extra[kind]:
            raise ValueError("Unexpected or missing command fields")
        if type(m["v"]) is not int or m["v"] != 1 or m["mode"] != self.mode:
            raise ValueError("Version/mode mismatch")
        decimal(m["request_id"], positive=True)
        for key in ("boot_id", "session_id"):
            if m[key] is None:
                if kind not in ("HELLO", "STOP"):
                    raise ValueError("Missing established identity")
            else:
                identity(m[key])
        if kind == "HELLO":
            if m["boot_id"] is not None or m["session_id"] is not None:
                raise ValueError("HELLO uses null bootstrap identities")
            identity(m["host_nonce"])
        if "not_after_us" in m:
            decimal(m["not_after_us"], positive=True)
        if "arm_generation" in m:
            decimal(m["arm_generation"])
        if kind == "CONFIGURE":
            decimal(m["config_generation"])
            self._configuration(m["config"])
        if kind == "ARM":
            identity(m["config_id"])
            identity(m["challenge"])
        if kind == "HEARTBEAT":
            if decimal(m["heartbeat_seq"], positive=True) != decimal(m["request_id"], positive=True):
                raise ValueError("Heartbeat request ID must equal its independent sequence")
        if kind in ("MOVE_TO", "SCAN"):
            for field in (("target_deg",) if kind == "MOVE_TO" else
                          ("end_deg", "plateau_start_deg", "plateau_end_deg", "plateau_speed_band_deg_s")):
                number(m[field])
            for field in ("speed_deg_s", "acceleration_deg_s2"):
                number(m[field])
            decimal(m["duration_us"], positive=True)
        return m

    def snapshot(self):
        f = self.feedback
        return {"mode": self.mode, "state": self.state, "faults": tuple(sorted(self.faults)),
                "boot_id": self.boot_id, "session_id": self.session_id,
                "arm_generation": str(self.arm_generation), "output_active": self.output.active,
                "lease_end_us": None if self.lease_end is None else str(self.lease_end),
                "config_generation": str(self.config_generation),
                "challenge": None if self.challenge is None else self.challenge[0],
                "challenge_end_us": None if self.challenge is None else str(self.challenge[1]),
                "feedback_fresh": self._now is not None and self._fresh(),
                "device_time_us": None if self._now is None else str(self._now),
                "sample_time_us": None if f is None else str(f.sample_us),
                "raw_count": None if f is None or f.count is None else str(f.count),
                "requested_angle_deg": self.target_deg,
                "motion_request_id": self.motion_request_id, "motion_kind": self.motion_kind,
                "reference_epoch": None if self._reference is None else self._reference.epoch,
                "angle_deg": f.angle_deg if f is not None and self._now is not None and self._fresh(reference=True) else None}

    def _configuration(self, value):
        keys = {"config_id", "calibration_id", "counts_per_qwp_rev", "max_speed_deg_s",
                "max_acceleration_deg_s2", "min_angle_deg", "max_angle_deg"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("Incomplete or unknown simulation configuration")
        for field in ("config_id", "calibration_id"):
            identity(value[field])
        for field in keys - {"config_id", "calibration_id"}:
            number(value[field])
        if (any(value[k] <= 0 for k in ("counts_per_qwp_rev", "max_speed_deg_s", "max_acceleration_deg_s2"))
                or value["min_angle_deg"] >= value["max_angle_deg"]):
            raise ValueError("Invalid simulation configuration range")
        return dict(value)

    def _current_session(self, m):
        return (self.session_id is not None and m["boot_id"] == self.boot_id
                and m["session_id"] == self.session_id)

    def _challenge(self):
        if (self.challenge is None and self.state in ("CONFIGURED", "STOPPED")
                and self._inside() and self._stationary()):
            self.challenge = (self._new_identity(), self._sum(self._now, self.timing.request_us))

    def _dispatch(self, m):
        kind = m["type"]
        if kind == "GET_STATUS":
            self._clean_protocol = True
            self._challenge()
        elif kind == "CONFIGURE":
            if (self.state not in ("DISARMED", "CONFIGURED", "STOPPED")
                    or not self._stationary()
                    or decimal(m["config_generation"]) != self.config_generation):
                raise ValueError("Configuration requires fresh stationary evidence and current generation")
            candidate = self._configuration(m["config"])
            if (self.config is not None and candidate["calibration_id"] == self.config["calibration_id"]
                    and candidate["counts_per_qwp_rev"] != self.config["counts_per_qwp_rev"]):
                raise ValueError("Changed count scaling requires a new calibration identity")
            if self.config_generation == U64:
                raise ValueError("Configuration generation exhausted")
            self._inhibit()
            if self.config is None or candidate["calibration_id"] != self.config["calibration_id"]:
                self._invalidate_reference()
            self.config = candidate
            self.config_generation += 1
            self.state = "CONFIGURED"
        elif kind == "ARM":
            if (self.state not in ("CONFIGURED", "STOPPED") or self.faults
                    or not self._inside() or not self._stationary()
                    or m["config_id"] != self.config["config_id"]
                    or self.challenge is None or m["challenge"] != self.challenge[0]
                    or self._now >= self.challenge[1]):
                raise ValueError("ARM requires current challenge, configuration and reference")
            if self.arm_generation == U64:
                self._fault("COUNTER_OVERFLOW")
                raise ValueError("Arming generation exhausted")
            self.arm_generation += 1
            self.challenge = None
            self.lease_end = self._sum(self._now, self.timing.lease_us)
            self.state = "ARMED"
        elif kind in ("MOVE_TO", "SCAN"):
            config = self.config
            if (self.state != "ARMED" or not self._inside()
                    or decimal(m["arm_generation"]) != self.arm_generation):
                raise ValueError("Motion requires a current armed reference")
            target = m["target_deg"] if kind == "MOVE_TO" else m["end_deg"]
            if (not config["min_angle_deg"] <= target <= config["max_angle_deg"]
                    or not 0 < m["speed_deg_s"] <= config["max_speed_deg_s"]
                    or not 0 < m["acceleration_deg_s2"] <= config["max_acceleration_deg_s2"]
                    or decimal(m["duration_us"], positive=True) > self.timing.motion_us):
                raise ValueError("Motion exceeds configured simulation limits")
            if kind == "SCAN":
                start = self.feedback.angle_deg
                direction = 1 if target > start else -1
                if (target == start or m["plateau_speed_band_deg_s"] <= 0
                        or not 0 <= direction * (m["plateau_start_deg"] - start)
                        < direction * (m["plateau_end_deg"] - start)
                        <= direction * (target - start)):
                    raise ValueError("Plateau must lie inside a finite directed scan")
                self.scan_plan = (direction, m["plateau_start_deg"], m["plateau_end_deg"],
                                  m["plateau_speed_band_deg_s"])
            self.motion_end = self._sum(self._now, decimal(m["duration_us"], positive=True))
            self.target_deg = target
            self.motion_request_id, self.motion_kind = m["request_id"], kind
            self.motion_limits = (m["speed_deg_s"], m["acceleration_deg_s2"])
            self.state = "RUNNING"
            self.output.enable()  # Boolean model state only; not PWM or a motor command.
        elif kind == "DISARM":
            self._inhibit()
            self.state = "FAULT" if self.faults else "DISARMED"
        elif kind == "CLEAR_FAULT":
            permanent = {"CLOCK", "TIME_OVERFLOW", "COUNTER_OVERFLOW", "IDENTITY"}
            predicates = {
                "FEEDBACK": self._inside(), "FEEDBACK_INTEGRITY": self._inside(),
                "TRAVEL_LIMIT": self._inside(), "EXTERNAL_INHIBIT": not self._external_inhibit,
                "CONTROL_DEADLINE": self._cycle_healthy, "PROTOCOL": self._clean_protocol,
                "RX_OVERFLOW": self._admission_ok and self._clean_protocol,
                "TX_OVERFLOW": self._admission_ok and self._clean_protocol,
                "ID_CONFLICT": not self._session_tainted,
                "SEQUENCE_EXHAUSTED": not self._session_exhausted,
                "LEASE_EXPIRED": self.lease_end is None,
                "MOTION_DURATION": self.motion_end is None,
                "STALL": self._inside(),
                "TRAVEL_PREDICTED": self._inside(),
            }
            unresolved = {cause for cause in self.faults if not predicates.get(cause, False)}
            if "STALL" in self.faults and not self._stall_release_ack:
                unresolved.add("STALL")
            if (self.state != "FAULT" or self.faults & permanent or unresolved
                    or not self._inside() or not self._stationary()):
                raise ValueError("Fault cause/reference evidence not cleared")
            self._inhibit()
            self.faults.clear()
            self.state = "DISARMED"
        else:
            raise ValueError("Unsupported simulation command")
        return "ACCEPTED"

    def consider_simulated_completion(self, *, position_band_deg, velocity_band_deg_s, dwell_us):
        if number(position_band_deg) <= 0 or number(velocity_band_deg_s) < 0:
            raise ValueError("Invalid simulation settling bands")
        uint(dwell_us)
        if self.state != "RUNNING":
            self._settled_since = None
            return False
        if (not self._inside() or abs(self.feedback.angle_deg - self.target_deg) > position_band_deg
                or abs(self.feedback.velocity_deg_s) > velocity_band_deg_s):
            self._settled_since = None
            return False
        if self._settled_since is None:
            self._settled_since = self.feedback.sample_us
        if self.feedback.sample_us - self._settled_since >= dwell_us:
            self._stop()
            return True
        return False

    def simulated_fault(self, cause):
        if cause not in ("STALL", "TRAVEL_PREDICTED", "OVERSPEED", "SIMULATION_TIME", "SIMULATION_NUMERICAL"):
            raise ValueError("Unknown plant-model fault")
        self._fault(cause)

    def acknowledge_simulated_stall_release(self):
        if self.output.active or "STALL" not in self.faults:
            raise ValueError("No inhibited simulated stall to acknowledge")
        self._stall_release_ack = True

    def _request(self, event, m):
        if not self._current_session(m):
            return "WRONG_SESSION"
        rid = decimal(m["request_id"], positive=True)
        previous = self._cache.get(rid)
        if previous is not None and previous[0] != event.payload:
            self._fault("ID_CONFLICT")
            return "ID_CONFLICT"
        if "not_after_us" in m:
            try:
                self._deadline(m["not_after_us"], self.timing.request_us)
            except ValueError:
                return "EXPIRED_OR_FUTURE"
        if previous is not None:
            return "RETRY:" + previous[1]  # No side effect; response also has current snapshot.
        if rid <= self._request_id:
            return "STALE_ID"
        self._request_id = rid
        try:
            outcome = self._dispatch(m)
        except ValueError:
            outcome = "REJECTED"
        self._cache[rid] = (event.payload, outcome)
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        return outcome

    def _heartbeat(self, event, m):
        if (not self._current_session(m) or self.state not in ("ARMED", "RUNNING")
                or decimal(m["arm_generation"]) != self.arm_generation):
            return "WRONG_ARM_OR_SESSION"
        seq = decimal(m["heartbeat_seq"], positive=True)
        if seq == self._heartbeat_seq and event.payload != self._heartbeat_payload:
            self._fault("ID_CONFLICT")
            return "ID_CONFLICT"
        if seq <= self._heartbeat_seq:
            return "STALE_HEARTBEAT"
        try:
            deadline = self._deadline(m["not_after_us"], self.timing.lease_us)
        except ValueError:
            return "EXPIRED_OR_FUTURE"
        self._heartbeat_seq, self._heartbeat_payload = seq, event.payload
        self.lease_end = deadline  # A valid newer heartbeat may conservatively shorten the lease.
        return "ACCEPTED"

    def _hello(self, m):
        self._inhibit()  # Even a repeated HELLO revokes before inspecting nonce/state.
        self.feedback = None
        self._encoder = self._stationary_since = None
        self._encoder_window.clear()
        self._invalidate_reference()
        self._session_tainted = self._session_exhausted = False
        self.state = "FAULT" if self.faults else "DISARMED"
        self.session_id = self._new_identity()
        self._cache.clear()
        self._request_id = 0
        # Each HELLO rotates session; no cached handshake transfer or motion replay.
        return "HELLO_REPLY"

    def cycle(self, events=(), *, now_us, feedback=None, rx_overflow=False,
              external_inhibit=False, tx_capacity=8):
        """One bounded simulated safety cycle, including calls with no input.

        Callers must poll even in silence. No RX/TX transport or real-time
        scheduling is supplied. Replies are snapshots, never proof of motion.
        """
        batch = tuple(islice(iter(events), MAX_EVENTS + 1))
        if (type(rx_overflow) is not bool or type(external_inhibit) is not bool
                or type(tx_capacity) is not int or not 0 <= tx_capacity <= 8):
            raise ValueError("Invalid bounded cycle admission options")
        try:
            uint(now_us)
        except ValueError:
            self._fault("CLOCK")
            return ()
        if self._last_time is not None and now_us < self._last_time:
            self._fault("CLOCK")
            return ()
        prior_time, self._last_time, self._now = self._last_time, now_us, now_us
        self._external_inhibit = external_inhibit
        self._cycle_healthy = prior_time is None or now_us - prior_time <= self.timing.cycle_us
        self._admission_ok = len(batch) <= MAX_EVENTS and not rx_overflow and len(batch) <= tx_capacity
        was_armed = self.state in ("ARMED", "RUNNING")
        prior_lease, prior_motion_end = self.lease_end, self.motion_end
        inhibited = False
        causes = set()
        if external_inhibit:
            causes.add("EXTERNAL_INHIBIT")
        if (self.state in ("ARMED", "RUNNING") and prior_time is not None
                and now_us - prior_time > self.timing.cycle_us):
            causes.add("CONTROL_DEADLINE")
        if feedback is not None:
            try:
                cause = self._ingest(feedback)
            except (ValueError, ArithmeticError):
                self.feedback = self._encoder = None
                self._stationary_since = None
                self._encoder_window.clear()
                cause = "FEEDBACK_INTEGRITY"
            if cause:
                causes.add(cause)
        if (self._reference is not None and (not self._fresh(reference=True)
                or self.feedback.calibration_id != self.config["calibration_id"])):
            self._invalidate_reference()
            self._stationary_since = None
            self._encoder_window.clear()
        try:
            for horizon in (self.timing.lease_us, self.timing.request_us, self.timing.motion_us):
                self._sum(now_us, horizon)
        except ValueError:
            causes.add("TIME_OVERFLOW")
        if self.challenge is not None and now_us >= self.challenge[1]:
            self.challenge = None
        if was_armed:
            if prior_lease is None or now_us >= prior_lease:
                causes.add("LEASE_EXPIRED")
            if not self._fresh(reference=True):
                causes.add("FEEDBACK")
            if prior_motion_end is not None and now_us >= prior_motion_end:
                causes.add("MOTION_DURATION")
        if (self._fresh(reference=True)
                and not self.config["min_angle_deg"] <= self.feedback.angle_deg <= self.config["max_angle_deg"]):
            causes.add("TRAVEL_LIMIT")  # Also observe coasting after output inhibition.
        for cause in sorted(causes):
            self._fault(cause)
            inhibited = True
        if len(batch) > MAX_EVENTS or rx_overflow:
            self._fault("RX_OVERFLOW")
            return ()
        parsed = []
        for event in batch:
            try:
                if isinstance(event, FrameError):
                    raise ValueError(event.detail)
                parsed.append((event, self._schema(event)))
            except ValueError:
                self._fault("PROTOCOL")
                inhibited = True
        # Discover payload conflicts for the entire batch before any enable.
        seen = {}
        for event, m in parsed:
            kind = m["type"]
            if kind in ("STOP", "HELLO") or not self._current_session(m):
                continue
            if kind == "HEARTBEAT" and decimal(m["arm_generation"]) != self.arm_generation:
                continue  # Stale arm traffic is rejected without renewal or conflict fault.
            rid = decimal(m["request_id"], positive=True)
            if rid == U64:
                self._fault("SEQUENCE_EXHAUSTED")
                inhibited = True
            key = ("heartbeat" if kind == "HEARTBEAT" else "request", rid)
            prior = seen.get(key)
            cached = self._cache.get(rid) if kind != "HEARTBEAT" else None
            conflict = ((prior is not None and prior != event.payload)
                        or (cached is not None and cached[0] != event.payload)
                        or (kind == "HEARTBEAT" and decimal(m["heartbeat_seq"]) == self._heartbeat_seq
                            and self._heartbeat_payload is not None and self._heartbeat_payload != event.payload))
            if conflict:
                self._fault("ID_CONFLICT")
                inhibited = True
            seen[key] = event.payload
        if self.arm_generation == U64:
            self._fault("COUNTER_OVERFLOW")
            inhibited = True
        # Reserve all replies before any lower-priority state mutation.
        if len(batch) > tx_capacity:
            self._fault("TX_OVERFLOW")
            inhibited = True
        stops = [(e, m) for e, m in parsed if m["type"] == "STOP"]
        if stops:
            for _ in stops:
                self._stop()
            inhibited = True
        disarms = [(e, m) for e, m in parsed if m["type"] == "DISARM" and self._current_session(m)]
        priority_outcomes = {}
        if not inhibited and disarms:
            for e, m in disarms:
                result = self._request(e, m)
                priority_outcomes[id(e)] = result
                if result == "ACCEPTED":
                    inhibited = True
        hellos = [(e, m) for e, m in parsed if m["type"] == "HELLO"]
        hello_outcome = None
        if not inhibited and hellos:
            try:
                if len(hellos) != 1:
                    raise ValueError("Ambiguous handshake batch")
                hello_outcome = self._hello(hellos[0][1])
            except ValueError:
                self._fault("IDENTITY")
                hello_outcome = "REJECTED"
            inhibited = True
        changing = [m for _, m in parsed if m["type"] in ("CONFIGURE", "ARM", "MOVE_TO", "SCAN", "CLEAR_FAULT")]
        responses = []
        for event, m in parsed:
            kind = m["type"]
            if kind == "STOP":
                outcome = "INHIBIT_REQUESTED"
            elif kind == "HELLO" and hello_outcome is not None:
                outcome = hello_outcome
            elif id(event) in priority_outcomes:
                outcome = priority_outcomes[id(event)]
            elif inhibited:
                outcome = "STATUS" if kind == "GET_STATUS" and self._current_session(m) else "BATCH_INHIBITED"
            elif len(changing) > 1 and kind in ("CONFIGURE", "ARM", "MOVE_TO", "SCAN", "CLEAR_FAULT"):
                outcome = "MULTIPLE_STATE_CHANGES"
            elif kind == "HEARTBEAT":
                outcome = self._heartbeat(event, m)
            else:
                try:
                    outcome = self._request(event, m)
                except ValueError:
                    self._fault("IDENTITY")
                    outcome = "REJECTED"
            # A late-discovered conflict must suppress subsequent batch enables too.
            inhibited = inhibited or bool(self.faults)
            response = {"request_id": m["request_id"], "request_type": kind, "outcome": outcome}
            if kind == "HELLO" and outcome == "HELLO_REPLY":
                response["host_nonce"] = m["host_nonce"]
            responses.append(response)
        if len(batch) > tx_capacity:
            return ()
        final = {**self.snapshot(), "snapshot_scope": "FINAL_CYCLE"}
        return tuple({**r, **final} for r in responses)
