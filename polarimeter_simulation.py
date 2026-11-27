"""Explicit SIMULATED motor/gear/count plant and PI harness; no hardware I/O.

Every parameter is supplied by the caller. None describes measured TSINY
hardware. The first-order plant is deliberately simple and has no current or
temperature model. Encoder estimates and optical/load truth are kept separate.
"""
from dataclasses import dataclass
import math
import random

from polarimeter_controller import EncoderSample, OfflineController, identity, number, uint


def clip(value, low, high):
    return min(high, max(low, value))


@dataclass(frozen=True)
class PlantParameters:
    gain_gearbox_deg_s: float
    time_constant_s: float
    counts_per_gearbox_rev: int
    external_ratio: float
    encoder_direction: int
    friction_duty: float
    backlash_qwp_deg: float
    optical_offset_deg: float
    count_noise: int
    max_dt_s: float

    def __post_init__(self):
        for value in (self.gain_gearbox_deg_s, self.time_constant_s, self.external_ratio, self.max_dt_s):
            if number(value) <= 0:
                raise ValueError("Positive simulated plant parameter required")
        for value in (self.friction_duty, self.backlash_qwp_deg):
            if number(value) < 0:
                raise ValueError("Negative simulated friction/backlash")
        if self.friction_duty >= 1:
            raise ValueError("Friction duty must be below one")
        uint(self.counts_per_gearbox_rev, positive=True)
        uint(self.count_noise)
        if self.count_noise > 10000:
            raise ValueError("Excessive simulated noise")
        number(self.optical_offset_deg)
        if type(self.encoder_direction) is not int or self.encoder_direction not in (-1, 1):
            raise ValueError("Invalid encoder sign")


class MotorPlant:
    mode = "SIMULATED"

    def __init__(self, parameters, *, continuity_id, seed):
        if type(parameters) is not PlantParameters or type(seed) is not int:
            raise TypeError("Explicit plant parameters and deterministic integer seed required")
        self.parameters = parameters
        self.continuity_id = identity(continuity_id)
        self.rng = random.Random(seed)
        self.gearbox_angle_deg = self.gearbox_velocity_deg_s = self.qwp_mechanical_deg = 0.0
        self.slip_deg = self.encoder_zero = 0.0
        self.load_duty = 0.0
        self.stalled = False

    @property
    def optical_fast_axis_deg(self):
        return self.qwp_mechanical_deg + self.parameters.optical_offset_deg + self.slip_deg

    def step(self, dt_s, duty):
        p = self.parameters
        if not 0 < number(dt_s) <= p.max_dt_s or not -1 <= number(duty) <= 1:
            raise ValueError("Simulated timestep/duty outside configured bounds")
        number(self.load_duty)
        if self.stalled:
            self.gearbox_velocity_deg_s = 0.0
            return
        # First-order ZOH integration. Friction is an input dead-zone approximation.
        net = duty - self.load_duty
        drive = math.copysign(max(0.0, abs(net) - p.friction_duty), net)
        target = p.gain_gearbox_deg_s * drive
        decay = math.exp(-dt_s / p.time_constant_s)
        previous = self.gearbox_velocity_deg_s
        new_velocity = number(target + (previous - target) * decay)
        new_angle = number(self.gearbox_angle_deg + target * dt_s + (previous - target) * p.time_constant_s * (1 - decay))
        self.gearbox_velocity_deg_s, self.gearbox_angle_deg = new_velocity, new_angle
        ideal = self.gearbox_angle_deg / p.external_ratio
        half = p.backlash_qwp_deg / 2
        self.qwp_mechanical_deg = clip(self.qwp_mechanical_deg, ideal - half, ideal + half)

    def sample(self, now_us, *, valid=True):
        p = self.parameters
        count = round((self.gearbox_angle_deg - self.encoder_zero) * p.counts_per_gearbox_rev / 360)
        count = p.encoder_direction * count + self.rng.randint(-p.count_noise, p.count_noise)
        return EncoderSample(now_us, count if valid else None, self.continuity_id, valid)

    def slip(self, delta_deg):
        self.slip_deg += number(delta_deg)  # Unobserved downstream optical holder slip.

    def reset_encoder(self, continuity_id):
        self.continuity_id = identity(continuity_id)
        self.encoder_zero = self.gearbox_angle_deg


@dataclass(frozen=True)
class PIParameters:
    kp: float
    ki: float
    integral_limit: float
    duty_limit: float

    def __post_init__(self):
        if number(self.kp) < 0 or number(self.ki) < 0:
            raise ValueError("Negative PI gain")
        if number(self.integral_limit) <= 0 or not 0 < number(self.duty_limit) <= 1:
            raise ValueError("Invalid simulated PI saturation limits")


class SpeedPI:
    def __init__(self, parameters):
        if type(parameters) is not PIParameters:
            raise TypeError("Explicit PI parameters required")
        self.parameters = parameters
        self.integral = 0.0
        self.saturated = False

    def reset(self):
        self.integral = 0.0
        self.saturated = False

    def step(self, target_deg_s, measured_deg_s, dt_s):
        if number(dt_s) <= 0:
            raise ValueError("PI requires positive elapsed time")
        p = self.parameters
        error = number(target_deg_s) - number(measured_deg_s)
        candidate = clip(self.integral + p.ki * error * dt_s, -p.integral_limit, p.integral_limit)
        trial = p.kp * error + candidate
        # Conditional integration: integrate only in-range or towards recovery.
        if abs(trial) <= p.duty_limit or (trial > p.duty_limit and error < 0) or (trial < -p.duty_limit and error > 0):
            self.integral = candidate
        raw = p.kp * error + self.integral
        self.saturated = abs(raw) > p.duty_limit
        return clip(raw, -p.duty_limit, p.duty_limit)


@dataclass(frozen=True)
class LoopParameters:
    position_gain_s_inv: float
    position_band_deg: float
    velocity_band_deg_s: float
    settle_us: int
    reversal_dwell_us: int
    braking_deceleration_deg_s2: float
    travel_margin_deg: float
    stall_duty: float
    stall_velocity_deg_s: float
    stall_us: int
    startup_us: int

    def __post_init__(self):
        for value in (self.position_gain_s_inv, self.position_band_deg, self.braking_deceleration_deg_s2):
            if number(value) <= 0:
                raise ValueError("Positive position/deceleration limit required")
        for value in (self.velocity_band_deg_s, self.travel_margin_deg, self.stall_velocity_deg_s):
            if number(value) < 0:
                raise ValueError("Invalid simulated loop band")
        if not 0 < number(self.stall_duty) <= 1:
            raise ValueError("Invalid stall duty")
        for value in (self.settle_us, self.reversal_dwell_us, self.stall_us, self.startup_us):
            uint(value)


class PositionTrajectory:
    def __init__(self, *, target, speed, acceleration, initial_speed, velocity_band,
                 reversal_dwell_us, position_band, position_gain):
        self.target, self.speed, self.acceleration = number(target), number(speed), number(acceleration)
        self.velocity_band, self.reversal_dwell_us = number(velocity_band), uint(reversal_dwell_us)
        self.command = number(initial_speed)
        self.position_band = number(position_band)
        self.position_gain = number(position_gain)
        self._direction = 0 if self.command == 0 else math.copysign(1, self.command)
        self._stationary_since = None
        self._pending_direction = None
        self.reset_pi = False
        if self.speed <= 0 or self.acceleration <= 0:
            raise ValueError("Positive trajectory speed/acceleration required")

    def step(self, angle, velocity, dt_s, now_us):
        uint(now_us)
        if number(dt_s) <= 0:
            raise ValueError("Trajectory timestep must be positive")
        error = self.target - number(angle)
        direction = 0 if error == 0 else math.copysign(1, error)
        desired = direction * min(self.speed, math.sqrt(2 * self.acceleration * abs(error)),
                                  self.position_gain * abs(error))
        if abs(error) <= self.position_band:
            desired = 0.0
        self.reset_pi = False
        if direction and self._direction and direction != self._direction:
            if self._pending_direction != direction:
                self._stationary_since = None
                self._pending_direction = direction
            desired = 0.0
            if abs(number(velocity)) <= self.velocity_band and abs(self.command) <= self.velocity_band:
                if self._stationary_since is None:
                    self._stationary_since = now_us
                if now_us - self._stationary_since >= self.reversal_dwell_us:
                    self._direction = direction
                    self._stationary_since = None
                    self.reset_pi = True
            else:
                self._stationary_since = None
        elif direction:
            self._direction = direction
            self._stationary_since = self._pending_direction = None
        else:
            self._stationary_since = self._pending_direction = None
        self.command += clip(desired - self.command, -self.acceleration * dt_s, self.acceleration * dt_s)
        return self.command


class SimulationHarness:
    mode = "SIMULATED"

    def __init__(self, controller, plant, pi, loop):
        if (type(controller) is not OfflineController or type(plant) is not MotorPlant
                or type(pi) is not SpeedPI or type(loop) is not LoopParameters):
            raise TypeError("Require explicit simulation components")
        self.controller, self.plant, self.pi, self.loop = controller, plant, pi, loop
        self._last_us = self._generation = self._started_us = self._stall_since = None
        self.trajectory = None
        self.completed = False
        self._motion = None

    def step(self, now_us, events=(), *, encoder_valid=True, external_inhibit=False):
        """Atomic host-facing result; completion/terminal events last one cycle."""
        self.completed = False
        try:
            result = self._step(now_us, events, encoder_valid=encoder_valid,
                                external_inhibit=external_inhibit)
        except (ValueError, ArithmeticError):
            self.controller.simulated_fault("SIMULATION_NUMERICAL")
            self.pi.reset()
            raise
        terminal = None
        if self._motion is not None and self.controller.state != "RUNNING":
            terminal = {**self._motion, "outcome": "COMPLETED" if self.completed else
                        "FAULT" if self.controller.state == "FAULT" else "CANCELLED",
                        "time_us": str(now_us)}
            self._motion = None
        final = {**self.controller.snapshot(), "snapshot_scope": "FINAL_HARNESS_CYCLE"}
        result.update(state=final["state"], duty=self.controller.output.duty,
                      output_active=final["output_active"], terminal=terminal,
                      motion_request_id=self.controller.motion_request_id,
                      motion_kind=self.controller.motion_kind,
                      replies=tuple({**r, **final} for r in result["replies"]))
        return result

    def _step(self, now_us, events=(), *, encoder_valid=True, external_inhibit=False):
        try:
            uint(now_us)
            if self._last_us is not None and now_us <= self._last_us:
                raise ValueError("Time did not advance")
        except ValueError:
            self.controller.simulated_fault("SIMULATION_TIME")
            self.pi.reset()
            raise ValueError("Plant steps require increasing integer time")
        dt = 0 if self._last_us is None else (now_us - self._last_us) / 1e6
        if dt:
            try:
                self.plant.step(dt, self.controller.output.duty)
            except (ValueError, ArithmeticError):
                self.controller.simulated_fault("SIMULATION_NUMERICAL")
                self.pi.reset()
                raise
        self._last_us = now_us
        try:
            sample = self.plant.sample(now_us, valid=encoder_valid)
            replies = self.controller.cycle(events, now_us=now_us, feedback=sample,
                                            external_inhibit=external_inhibit)
        except (ValueError, ArithmeticError):
            self.controller.simulated_fault("SIMULATION_NUMERICAL")
            self.pi.reset()
            raise
        c, p = self.controller, self.loop
        if c.state == "RUNNING" and self._motion is None:
            self._motion = dict(request_id=c.motion_request_id, session_id=c.session_id,
                                boot_id=c.boot_id, arm_generation=str(c.arm_generation))
        if c._fresh(reference=True):
            angle, velocity = c.feedback.angle_deg, c.feedback.velocity_deg_s
            distance = c.config["max_angle_deg"] - angle if velocity > 0 else angle - c.config["min_angle_deg"]
            stopping_distance = velocity**2 / (2 * p.braking_deceleration_deg_s2) + p.travel_margin_deg
            if abs(velocity) > c.config["max_speed_deg_s"]:
                c.simulated_fault("OVERSPEED")
            elif abs(velocity) > p.velocity_band_deg_s and distance <= stopping_distance:
                c.simulated_fault("TRAVEL_PREDICTED")
        if c.state == "RUNNING" and dt:
            if self._generation != c.arm_generation:
                self._generation, self._started_us = c.arm_generation, now_us
                self.trajectory = PositionTrajectory(target=c.target_deg, speed=c.motion_limits[0],
                    acceleration=c.motion_limits[1], initial_speed=c.feedback.velocity_deg_s,
                    velocity_band=p.velocity_band_deg_s, reversal_dwell_us=p.reversal_dwell_us,
                    position_band=p.position_band_deg, position_gain=p.position_gain_s_inv)
                self._stall_since = None
                self.pi.reset()
                self.completed = False
            angle, velocity = c.feedback.angle_deg, c.feedback.velocity_deg_s
            direction = 1 if c.target_deg > angle else -1
            distance = c.config["max_angle_deg"] - angle if direction > 0 else angle - c.config["min_angle_deg"]
            stopping_distance = velocity**2 / (2 * p.braking_deceleration_deg_s2) + p.travel_margin_deg
            if c.target_deg != angle and distance <= p.travel_margin_deg:
                c.simulated_fault("TRAVEL_PREDICTED")
            elif c.consider_simulated_completion(position_band_deg=p.position_band_deg,
                    velocity_band_deg_s=p.velocity_band_deg_s, dwell_us=p.settle_us):
                self.completed = True
            else:
                try:
                    command = self.trajectory.step(angle, velocity, dt, now_us)
                    if self.trajectory.reset_pi:
                        self.pi.reset()
                    duty = self.pi.step(command, velocity, dt)
                    c.output.set_duty(duty)
                except (ValueError, ArithmeticError):
                    c.simulated_fault("SIMULATION_NUMERICAL")
                    self.pi.reset()
                    raise
                if (now_us - self._started_us >= p.startup_us and abs(duty) >= p.stall_duty
                        and abs(velocity) <= p.stall_velocity_deg_s):
                    if self._stall_since is None:
                        self._stall_since = now_us
                    elif now_us - self._stall_since >= p.stall_us:
                        c.simulated_fault("STALL")
                else:
                    self._stall_since = None
        if c.state != "RUNNING":
            c.output.set_duty(0.0)
            self.pi.reset()
            self._generation = self._stall_since = None
        return {"mode": self.mode, "time_us": now_us, "state": c.state,
                "duty": c.output.duty, "gearbox_truth_deg": self.plant.gearbox_angle_deg,
                "qwp_mechanical_truth_deg": self.plant.qwp_mechanical_deg,
                "optical_fast_axis_truth_deg": self.plant.optical_fast_axis_deg,
                "encoder_count": None if c.feedback is None else c.feedback.count,
                "estimated_qwp_deg": None if c.feedback is None else c.feedback.angle_deg,
                "estimated_velocity_deg_s": None if c.feedback is None else c.feedback.velocity_deg_s,
                "completed": self.completed, "replies": replies}
