"""Explicit SIMULATED scenarios: invented parameters, never physical defaults."""
from collections import deque
from dataclasses import replace
import itertools
import math

from polarimeter_controller import (OfflineController, Readiness, SimulatedReference, Timing)
from polarimeter_protocol import FrameMessage, encode_frame
from polarimeter_simulation import (LoopParameters, MotorPlant, PIParameters,
                                    PlantParameters, SimulationHarness, SpeedPI)


class Scenario:
    def __init__(self, *, dt_us=5000, backlash=0.0, noise=0, counts=36000,
                 direction=1, pi_parameters=None):
        self.dt_us, self.now, self.request_id, self.heartbeat_seq = dt_us, 0, 0, 0
        ids = itertools.count(100)
        self.controller = OfflineController(
            timing=Timing(lease_us=200000, request_us=200000, feedback_us=50000,
                          cycle_us=20000, motion_us=20000000),
            readiness=Readiness(stationary_band_deg_s=.1, stationary_dwell_us=10000,
                                max_count_rate_s=5e6, velocity_window_us=20000),
            boot_id="1" * 32, id_factory=lambda: f"{next(ids):032x}")
        self.plant = MotorPlant(PlantParameters(gain_gearbox_deg_s=180, time_constant_s=.12,
            counts_per_gearbox_rev=counts, external_ratio=1.5, encoder_direction=direction,
            friction_duty=.02, backlash_qwp_deg=backlash, optical_offset_deg=5,
            count_noise=noise, max_dt_s=.02), continuity_id="a" * 32, seed=12)
        self.pi = SpeedPI(pi_parameters or PIParameters(kp=.012, ki=.08,
                                                       integral_limit=.7, duty_limit=.8))
        self.loop = LoopParameters(position_gain_s_inv=2, position_band_deg=.08, velocity_band_deg_s=.2,
            settle_us=200000, reversal_dwell_us=50000, braking_deceleration_deg_s2=200,
            travel_margin_deg=1, stall_duty=.7, stall_velocity_deg_s=.1,
            stall_us=500000, startup_us=500000)
        self.harness = SimulationHarness(self.controller, self.plant, self.pi, self.loop)
        self.config = dict(config_id="2" * 32, calibration_id="3" * 32,
            counts_per_qwp_rev=counts * 1.5, max_speed_deg_s=60,
            max_acceleration_deg_s2=100, min_angle_deg=-360, max_angle_deg=720)
        self.harness.step(0, [self.message("HELLO")])
        # Count samples establish stationary evidence before CONFIGURE/reference.
        for _ in range(max(4, math.ceil(20000 / dt_us))):
            self.tick(heartbeat=False)
        self.tick([self.message("CONFIGURE", config_generation="0", config=self.config)], heartbeat=False)
        if self.controller.state != "CONFIGURED":
            raise AssertionError(("Fixture failed to configure", self.controller.snapshot()))
        self.controller.establish_reference(SimulatedReference("6" * 32, "3" * 32, 0, 0, direction))
        self.tick([self.message("GET_STATUS")], heartbeat=False)

    def message(self, kind, **fields):
        self.request_id += 1
        m = dict(v=1, mode="SIMULATED", type=kind, request_id=str(self.request_id),
                 boot_id=self.controller.boot_id, session_id=self.controller.session_id)
        if kind == "HELLO":
            m.update(boot_id=None, session_id=None, host_nonce="4" * 32)
        elif kind not in ("STOP", "GET_STATUS"):
            m["not_after_us"] = str(self.now + 150000)
        m.update(fields)
        return FrameMessage(encode_frame(m))

    def tick(self, events=(), *, heartbeat=True, encoder_valid=True, external_inhibit=False):
        self.now += self.dt_us
        batch = list(events)
        if (heartbeat and self.controller.state in ("ARMED", "RUNNING")
                and self.now % 50000 < self.dt_us):
            self.heartbeat_seq += 1
            batch.append(self.message("HEARTBEAT", request_id=str(self.heartbeat_seq),
                heartbeat_seq=str(self.heartbeat_seq), arm_generation=str(self.controller.arm_generation),
                not_after_us=str(self.now + 200000)))
        return self.harness.step(self.now, batch, encoder_valid=encoder_valid,
                                 external_inhibit=external_inhibit)

    def start(self, target=90, *, speed=30, acceleration=60, duration_us=12000000):
        c = self.controller
        if c.challenge is None:
            self.tick([self.message("GET_STATUS")], heartbeat=False)
        self.tick([self.message("ARM", config_id=self.config["config_id"], challenge=c.challenge[0])])
        result = self.tick([self.message("MOVE_TO", arm_generation=str(c.arm_generation),
            target_deg=target, speed_deg_s=speed, acceleration_deg_s2=acceleration,
            duration_us=str(duration_us))])
        if result["state"] != "RUNNING":
            raise AssertionError(("Fixture did not start", result, c.snapshot()))
        return result


def position_report(*, dt_us=5000, target=90, backlash=0.0, noise=0):
    s = Scenario(dt_us=dt_us, backlash=backlash)
    s.start(target)
    start = s.now
    overshoot = 0.0
    plateau = deque(maxlen=1000)
    command = 30 if target > 0 else -30
    completed_at = None
    result = None
    for _ in range(math.ceil(12e6 / dt_us)):
        if noise and s.now - start >= 1000000:
            s.plant.parameters = replace(s.plant.parameters, count_noise=noise)
        result = s.tick()
        elapsed = (s.now - start) / 1e6
        angle = result["estimated_qwp_deg"]
        if angle is not None:
            overshoot = max(overshoot, (angle - target) * (1 if target > 0 else -1))
        if 1 <= elapsed <= 2 and result["estimated_velocity_deg_s"] is not None:
            plateau.append(result["estimated_velocity_deg_s"])
        if result["state"] == "FAULT":
            break
        if result["completed"]:
            completed_at = elapsed
            break
    if result is None:
        raise AssertionError("No scenario samples")
    mean_speed = sum(plateau) / len(plateau) if plateau else None
    ripple = max(plateau) - min(plateau) if plateau else None
    return dict(mode="SIMULATED", dt_us=dt_us, target_deg=target, backlash_deg=backlash,
        count_noise=noise, completed_s=completed_at, final_state=result["state"],
        noise_start_s=1 if noise else None,
        final_estimated_error_deg=result["estimated_qwp_deg"] - target if result["estimated_qwp_deg"] is not None else None,
        mechanical_truth_error_deg=result["qwp_mechanical_truth_deg"] - target,
        optical_minus_encoder_deg=result["optical_fast_axis_truth_deg"] - result["estimated_qwp_deg"]
            if result["estimated_qwp_deg"] is not None else None,
        peak_overshoot_deg=overshoot, plateau_mean_velocity_deg_s=mean_speed,
        plateau_speed_error_deg_s=mean_speed - command if mean_speed is not None else None,
        plateau_peak_to_peak_ripple_deg_s=ripple, faults=sorted(s.controller.faults))


def stop_report():
    s = Scenario()
    s.start()
    for _ in range(200):
        s.tick()
    s.tick([s.message("STOP")])
    angle = s.plant.qwp_mechanical_deg
    velocity = s.plant.gearbox_velocity_deg_s / s.plant.parameters.external_ratio
    for _ in range(200):
        s.tick()
    return dict(mode="SIMULATED", stop_velocity_deg_s=velocity,
                one_second_coast_deg=s.plant.qwp_mechanical_deg - angle)


if __name__ == "__main__":
    import json
    from dataclasses import asdict
    fixture = Scenario()
    print(json.dumps(dict(mode="SIMULATED", measured_hardware=False,
        plant=asdict(fixture.plant.parameters), pi=asdict(fixture.pi.parameters),
        loop=asdict(fixture.loop), readiness=asdict(fixture.controller.readiness),
        timing=asdict(fixture.controller.timing), config=fixture.config,
        scenarios=[position_report(**options) for options in (
            {}, {"dt_us": 2500}, {"backlash": 1}, {"noise": 1}, {"target": -90})],
        stop=stop_report()), indent=2, sort_keys=True))
