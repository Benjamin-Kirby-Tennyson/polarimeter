"""DC motor fixtures are SIMULATED, not measurements or physical tuning."""
from dataclasses import replace
import math
import subprocess
import sys
import unittest

from polarimeter_simulation import MotorPlant, PIParameters, PositionTrajectory, SpeedPI
from tests.simulation_fixtures import Scenario, position_report


class PlantTests(unittest.TestCase):
    def test_first_order_zoh_matches_analytic_solution(self):
        parameters = replace(Scenario().plant.parameters, friction_duty=0)
        results = []
        for dt in (.01, .005):
            plant = MotorPlant(parameters, continuity_id="a" * 32, seed=1)
            for _ in range(round(1 / dt)):
                plant.step(dt, .5)
            tau = parameters.time_constant_s
            target = .5 * parameters.gain_gearbox_deg_s
            self.assertAlmostEqual(plant.gearbox_velocity_deg_s, target * (1 - math.exp(-1 / tau)), places=10)
            self.assertAlmostEqual(plant.gearbox_angle_deg, target * (1 - tau * (1 - math.exp(-1 / tau))), places=9)
            results.append(plant.gearbox_angle_deg)
        self.assertAlmostEqual(*results, places=9)

    def test_duty_inhibit_does_not_instantly_stop_inertial_plant(self):
        s = Scenario()
        s.start()
        for _ in range(200):
            s.tick()
        before = s.plant.qwp_mechanical_deg
        stopped = s.tick([s.message("STOP")])
        self.assertEqual(stopped["duty"], 0)
        self.assertEqual(stopped["state"], "STOPPED")
        velocity = s.plant.gearbox_velocity_deg_s
        self.assertGreater(velocity, 0)
        for _ in range(40):
            s.tick()
        self.assertGreater(s.plant.qwp_mechanical_deg, before)
        self.assertGreater(s.plant.gearbox_velocity_deg_s, 0)
        self.assertLess(s.plant.gearbox_velocity_deg_s, velocity)

    def test_external_ratio_and_encoder_direction_applied_once(self):
        s = Scenario(direction=-1)
        s.start()
        for _ in range(150):
            result = s.tick()
        expected = s.plant.gearbox_angle_deg / s.plant.parameters.external_ratio
        self.assertLess(result["encoder_count"], 0)
        self.assertAlmostEqual(result["estimated_qwp_deg"], expected, delta=.004)
        self.assertAlmostEqual(result["qwp_mechanical_truth_deg"], expected, places=9)

    def test_backlash_and_downstream_slip_are_not_encoder_measurements(self):
        s = Scenario(backlash=1)
        s.start()
        for _ in range(200):
            result = s.tick()
        self.assertAlmostEqual(result["estimated_qwp_deg"] - result["qwp_mechanical_truth_deg"], .5, delta=.004)
        counts_before = s.plant.sample(s.now).count
        optical_before = s.plant.optical_fast_axis_deg
        s.plant.slip(10)
        self.assertEqual(s.plant.sample(s.now).count, counts_before)
        self.assertEqual(s.plant.optical_fast_axis_deg, optical_before + 10)
        self.assertNotEqual(s.controller.state, "FAULT")  # No invented slip detector.

    def test_low_speed_count_quantization_and_explicit_noise(self):
        p = replace(Scenario().plant.parameters, counts_per_gearbox_rev=36, friction_duty=0, count_noise=0)
        plant = MotorPlant(p, continuity_id="a" * 32, seed=2)
        self.assertEqual(plant.sample(0).count, 0)
        plant.step(.005, .001)
        self.assertGreater(plant.gearbox_angle_deg, 0)
        self.assertEqual(plant.sample(5000).count, 0)
        plant.parameters = replace(p, count_noise=1)
        values = {plant.sample(i).count for i in range(30)}
        self.assertTrue(values <= {-1, 0, 1})
        self.assertGreater(len(values), 1)

    def test_invalid_timestep_or_duty_is_rejected_without_plant_change(self):
        p = Scenario().plant
        for dt, duty in ((0, .1), (.1, .1), (.01, 1.1), (.01, float('nan'))):
            with self.assertRaises(ValueError):
                p.step(dt, duty)
            self.assertEqual(p.gearbox_angle_deg, 0)


class ControlTests(unittest.TestCase):
    def test_pi_saturates_without_windup_and_resets(self):
        pi = SpeedPI(PIParameters(kp=.1, ki=1, integral_limit=.5, duty_limit=.8))
        for _ in range(100):
            self.assertEqual(pi.step(100, 0, .01), .8)
        self.assertEqual(pi.integral, 0)
        self.assertTrue(pi.saturated)
        self.assertEqual(pi.step(0, 0, .01), 0)
        pi.step(1, 0, .01)
        self.assertGreater(pi.integral, 0)
        pi.reset()
        self.assertEqual(pi.integral, 0)

    def test_trajectory_acceleration_speed_and_reversal_dwell(self):
        trajectory = PositionTrajectory(target=-10, speed=10, acceleration=20, initial_speed=5,
            velocity_band=.1, reversal_dwell_us=100000, position_band=.08, position_gain=2)
        previous = trajectory.command
        for i in range(30):
            command = trajectory.step(0, 5, .01, i * 10000)
            self.assertGreaterEqual(command, 0)
            self.assertLessEqual(abs(command - previous), .2000001)
            previous = command
        for i in range(10):
            self.assertGreaterEqual(trajectory.step(0, 0, .01, 300000 + i * 10000), 0)
        trajectory.step(0, 0, .01, 400000)
        self.assertLess(trajectory.step(0, 0, .01, 410000), 0)
        self.assertTrue(abs(trajectory.command) <= 10)

    def test_position_completion_and_timestep_convergence(self):
        coarse, fine = position_report(), position_report(dt_us=2500)
        for report in (coarse, fine):
            self.assertEqual(report["mode"], "SIMULATED")
            self.assertEqual(report["final_state"], "STOPPED")
            self.assertIsNotNone(report["completed_s"])
            self.assertLess(abs(report["final_estimated_error_deg"]), .08)
            self.assertLess(abs(report["plateau_speed_error_deg_s"]), .2)
        self.assertLess(abs(coarse["completed_s"] - fine["completed_s"]), .05)
        self.assertLess(abs(coarse["mechanical_truth_error_deg"] - fine["mechanical_truth_error_deg"]), .01)

    def test_backlash_exposes_optical_repeatability_limit(self):
        report = position_report(backlash=1)
        self.assertEqual(report["final_state"], "STOPPED")
        self.assertLess(abs(report["final_estimated_error_deg"]), .08)
        self.assertGreater(abs(report["mechanical_truth_error_deg"]), .5)

    def test_negative_position_and_post_completion_rearm(self):
        report = position_report(target=-90)
        self.assertEqual(report["final_state"], "STOPPED")
        self.assertLess(abs(report["final_estimated_error_deg"]), .08)
        s = Scenario()
        s.start(target=10)
        for _ in range(2000):
            result = s.tick()
            if result["completed"]:
                break
        self.assertEqual(result["state"], "STOPPED")
        self.assertIsNone(s.controller.lease_end)
        self.assertEqual(s.pi.integral, 0)
        rejected = s.tick([s.message("MOVE_TO", arm_generation=str(s.controller.arm_generation),
            target_deg=20, speed_deg_s=30, acceleration_deg_s2=60, duration_us="12000000")])
        self.assertEqual(rejected["state"], "STOPPED")

    def test_heartbeat_loss_feedback_loss_and_encoder_reset(self):
        for failure in ("heartbeat", "feedback", "reset"):
            s = Scenario()
            s.start()
            for _ in range(100):
                s.tick()
            if failure == "reset":
                s.plant.reset_encoder("b" * 32)
            for _ in range(60):
                result = s.tick(heartbeat=failure != "heartbeat", encoder_valid=failure != "feedback")
                if result["state"] == "FAULT":
                    break
            self.assertEqual(result["state"], "FAULT")
            self.assertEqual(result["duty"], 0)
            self.assertIsNone(s.controller.challenge)
            self.assertEqual(s.pi.integral, 0)

    def test_stall_and_load_disturbance_are_explicit_simulations(self):
        s = Scenario()
        s.start()
        s.plant.stalled = True
        for _ in range(500):
            result = s.tick()
            if result["state"] == "FAULT":
                break
        self.assertIn("STALL", s.controller.faults)
        self.assertEqual(result["duty"], 0)
        s = Scenario()
        s.start()
        for _ in range(200):
            s.tick()
        s.plant.load_duty = .1
        for _ in range(150):
            result = s.tick()
        self.assertEqual(result["state"], "RUNNING")
        self.assertAlmostEqual(result["estimated_velocity_deg_s"], 30, delta=1)

    def test_prediction_inhibits_before_inferred_travel_edge(self):
        s = Scenario()
        s.start()
        for _ in range(200):
            s.tick()
        angle = s.controller.feedback.angle_deg
        # Narrow only this simulated envelope to exercise the predictor.
        s.controller.config = {**s.controller.config, "max_angle_deg": angle + 2}
        result = s.tick()
        self.assertIn("TRAVEL_PREDICTED", s.controller.faults)
        self.assertEqual(result["duty"], 0)
        self.assertGreater(s.plant.gearbox_velocity_deg_s, 0)

    def test_simulator_imports_no_serial_or_heavy_packages(self):
        code = ("import sys; import polarimeter_simulation; "
                "assert not {'serial','numpy','scipy','matplotlib'} & set(sys.modules)")
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quantized_noise_can_fail_settling_without_false_completion(self):
        report = position_report(noise=1)
        self.assertIsNone(report["completed_s"])
        self.assertEqual(report["final_state"], "FAULT")
        self.assertIn("MOTION_DURATION", report["faults"])
        self.assertLess(abs(report["final_estimated_error_deg"]), .08)
        self.assertGreater(report["plateau_peak_to_peak_ripple_deg_s"], .6667)

    def test_two_moves_with_backlash_have_direction_dependent_truth_error(self):
        s = Scenario(backlash=1)
        errors = []
        for target in (30, -30):
            s.start(target=target)
            for _ in range(2400):
                result = s.tick()
                if result["completed"] or result["state"] == "FAULT":
                    break
            self.assertEqual(result["state"], "STOPPED")
            errors.append(result["estimated_qwp_deg"] - result["qwp_mechanical_truth_deg"])
            for _ in range(100):
                s.tick()
        self.assertAlmostEqual(errors[0], .5, delta=.004)
        self.assertAlmostEqual(errors[1], -.5, delta=.004)

    def test_invalid_harness_time_inhibits_before_exception(self):
        for same_time in (True, False):
            s = Scenario()
            s.start()
            with self.assertRaises(ValueError):
                s.harness.step(s.now if same_time else s.now + 100000)
            self.assertEqual(s.controller.state, "FAULT")
            self.assertEqual(s.controller.output.duty, 0)
            self.assertEqual(s.pi.integral, 0)

    def test_stall_clear_requires_explicit_simulated_release_acknowledgment(self):
        s = Scenario()
        s.start()
        s.plant.stalled = True
        for _ in range(500):
            result = s.tick()
            if result["state"] == "FAULT":
                break
        self.assertIn("STALL", s.controller.faults)
        result = s.tick([s.message("CLEAR_FAULT")])
        self.assertEqual(result["state"], "FAULT")
        s.plant.stalled = False
        s.controller.acknowledge_simulated_stall_release()
        result = s.tick([s.message("CLEAR_FAULT")])
        self.assertEqual(result["state"], "DISARMED")
        self.assertEqual(result["duty"], 0)

    def test_count_overflow_exception_inhibits_simulated_output(self):
        s = Scenario()
        s.start()
        s.plant.parameters = replace(s.plant.parameters, counts_per_gearbox_rev=2**64 - 1)
        s.plant.gearbox_angle_deg = 1e8
        with self.assertRaises(ValueError):
            s.tick()
        self.assertIn("SIMULATION_NUMERICAL", s.controller.faults)
        self.assertEqual(s.controller.output.duty, 0)
        self.assertFalse(s.controller.output.active)
        self.assertEqual(s.pi.integral, 0)
