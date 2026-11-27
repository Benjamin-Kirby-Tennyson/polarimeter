"""Framed host and optical integration tests use only explicit in-memory simulation."""
import csv
from dataclasses import replace
import json
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from polarimeter_controller import EncoderSample
from polarimeter_optical import OpticalAcquisition, OpticalClock
from polarimeter_protocol import FrameMessage, encode_frame
from polarimeter_rotation import MemoryRotationLink, RotationHost, simulated_demo
from polarimeter_rotation_session import RotationSession, RotationWorker
from tests.simulation_fixtures import Scenario


def linked():
    harness, config, plan = simulated_demo()
    link = MemoryRotationLink(harness, permit_simulated_reference=True)
    host = RotationHost(link, config, plan)
    host.start(0)
    return link, host


def running():
    link, host = linked()
    for now in range(0, 100001, 5000):
        host.tick(now)
    if host.phase != "SCANNING":
        raise AssertionError(host.detail)
    return link, host, 100000


class HostTests(unittest.TestCase):
    def test_request_acceptance_is_not_completion_and_one_arm_scans(self):
        link, host, now = running()
        self.assertTrue(host.accepted_motion)
        self.assertIsNone(host.terminal)
        self.assertEqual(link.harness.controller.motion_kind, "SCAN")
        self.assertEqual(link.harness.controller.target_deg, 1080)
        self.assertEqual(link.harness.controller.scan_plan[:3], (1, 180, 900))
        self.assertLessEqual(len(host.pending), 8)
        host.cancel(now)
        host.tick(now + 5000)
        self.assertEqual(host.phase, "CANCELLED")
        self.assertFalse(link.harness.controller.output.active)
        self.assertIsNone(link.harness.controller.lease_end)

    def test_cancel_before_hello_or_arm_cannot_be_lost(self):
        for before in (True, False):
            link, host = linked()
            if not before:
                host.tick(0)
            host.cancel(0)
            host.tick(5000)
            self.assertEqual(host.phase, "CANCELLED")
            self.assertEqual(link.harness.controller.output.enable_count, 0)

    def test_lost_ack_is_retried_exactly_without_reexecuting(self):
        link, host = linked()
        # Lose the CONFIGURE response, not the request, and retain later status.
        lost = False
        for now in range(0, 400001, 5000):
            if host.phase == "CONFIGURING" and not lost:
                link.drop_next = True
                lost = True
            host.tick(now)
        self.assertTrue(lost)
        self.assertEqual(host.phase, "SCANNING", host.detail)
        self.assertEqual(link.harness.controller.config_generation, 1)
        self.assertEqual(link.harness.controller.output.enable_count, 1)

    def test_status_loss_latches_unknown_and_device_lease_still_expires(self):
        link, host, now = running()
        link.suppress_responses = True
        link.connected = False
        for t in range(now + 5000, now + 400001, 5000):
            host.tick(t)
        self.assertEqual(host.phase, "UNKNOWN")
        self.assertIsNone(host.latest)
        self.assertFalse(link.harness.controller.output.active)
        self.assertIn("LEASE_EXPIRED", link.harness.controller.faults)
        link.connected = True
        link.suppress_responses = False
        host.tick(now + 405000)
        self.assertEqual(host.phase, "UNKNOWN")

    def test_heartbeat_request_loss_is_observed_as_device_fault(self):
        link, host, now = running()
        link.drop_heartbeats = True
        for t in range(now + 5000, now + 350001, 5000):
            host.tick(t)
        self.assertIn(host.phase, ("FAULT", "UNKNOWN"))
        self.assertIn("LEASE_EXPIRED", link.harness.controller.faults)
        self.assertFalse(link.harness.controller.output.active)

    def test_corrupt_response_inhibits_and_does_not_restore_latest_state(self):
        link, host, now = running()
        link.corrupt_next = True
        host.tick(now + 5000)
        self.assertEqual(host.phase, "UNKNOWN")
        self.assertIsNone(host.latest)
        host.tick(now + 10000)
        self.assertFalse(link.harness.controller.output.active)
        self.assertEqual(host.phase, "UNKNOWN")

    def test_duplicate_response_is_ignored_and_conflicting_duplicate_rejected(self):
        link, host, now = running()
        link.duplicate_next = True
        host.tick(now + 5000)
        seq = host._sequence
        host.tick(now + 10000)
        self.assertEqual(host._sequence, seq)
        self.assertEqual(host.phase, "SCANNING")
        changed = dict(host.latest)
        changed["truth"] += 1
        with self.assertRaises(ValueError):
            host._receive(encode_frame(changed), now + 10000)

    def test_response_schema_mode_session_and_clock_are_validated(self):
        cases = ({"mode": "HARDWARE"}, {"boot_id": "8" * 32}, {"time_us": "999999999"},
                 {"active": 1}, {"duty": 2}, {"extra": 1}, {"ack": [[[1], "1", "ACCEPTED"]]},
                 {"terminal": ["123", "COMPLETED", "9" * 32]})
        for fields in cases:
            link, host, now = running()
            packet = dict(host.latest)
            packet.update(fields)
            packet["seq"] = str(host._sequence + 1)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                host._receive(encode_frame(packet), now)

    def test_response_delay_is_bounded_and_late_ack_cannot_resume(self):
        link, host = linked()
        link.delay_us = 200000
        for now in range(0, 400001, 5000):
            host.tick(now)
        self.assertEqual(host.phase, "UNKNOWN")
        self.assertFalse(link.harness.controller.output.active)
        self.assertLessEqual(len(link.pending_rx), 32)

    def test_memory_queues_fault_closed_when_full(self):
        link, host, now = running()
        frame = encode_frame(dict(v=1, type="GET_STATUS", mode="SIMULATED", request_id="100",
                                  boot_id=host.boot_id, session_id=host.session_id))
        for _ in range(8):
            link.send(frame)
        with self.assertRaises(BufferError):
            link.send(frame)
        self.assertFalse(link.harness.controller.output.active)


class OpticalTests(unittest.TestCase):
    def acquisition(self, mapping=True):
        clock = OpticalClock(10000, 50, 100)
        return OpticalAcquisition(plan=dict(end_deg=1080, plateau_start_deg=180, plateau_end_deg=900,
            speed_deg_s=30, plateau_speed_band_deg_s=1), exposure_us=10000, period_us=20000,
            detector_clock=clock, clock_mapping=clock if mapping else None)

    def packet(self, i, **changes):
        t = i * 5000
        angle = 180 + 30 * t / 1e6
        p = dict(mode="SIMULATED", seq=str(i + 1), time_us=str(t), count=str(round(angle * 150)),
            angle=angle, velocity=30, truth=angle + 5, boot_id="1"*32, session_id="2"*32, generation="1", motion="5", ref="6" * 32, kind="SCAN", state="RUNNING")
        p.update(changes)
        return p

    def test_exposure_is_time_bracketed_and_intensity_uses_optical_truth(self):
        a = self.acquisition()
        samples = [s for i in range(30) if (s := a.record(self.packet(i))) is not None and s["quality"] == "VALID"]
        self.assertGreater(len(samples), 3)
        s = samples[-1]
        self.assertLessEqual(s["encoder_low_us"], s["midpoint_us"])
        self.assertGreaterEqual(s["encoder_high_us"], s["midpoint_us"])
        self.assertAlmostEqual(s["optical_truth_deg"] - s["estimated_angle_deg"], 5, delta=.0001)
        self.assertAlmostEqual(s["intensity_au"], a.model.intensity(s["optical_truth_deg"]), delta=.0001)
        self.assertLess(s["clock_error_us"], 1)
        self.assertAlmostEqual(s["timing_angle_bound_deg"], .0031)

    def test_gap_in_endpoint_bracket_is_not_silently_interpolated(self):
        a = self.acquisition()
        flagged = []
        for i in range(20):
            if i == 4:
                continue
            sample = a.record(self.packet(i))
            if sample is not None:
                flagged.append(sample["quality"])
        self.assertIn("SEQUENCE_GAP", flagged)

    def test_reference_change_and_reversal_invalidate_exposure(self):
        for mutation in ("reference", "reversal"):
            a = self.acquisition()
            qualities = []
            for i in range(30):
                changes = {"ref": "7" * 32} if mutation == "reference" and i >= 3 else {}
                if mutation == "reversal" and i == 3:
                    changes.update(velocity=-30, angle=180)
                s = a.record(self.packet(i, **changes))
                if s is not None:
                    qualities.append(s["quality"])
            self.assertTrue(any(q in ("REFERENCE_OR_MOTION_CHANGED", "NOT_STEADY_ROTATION", "REVERSAL_IN_EXPOSURE") for q in qualities))

    def test_clock_mapping_missing_or_drift_error_cannot_be_called_valid(self):
        for missing in (True, False):
            a = self.acquisition(mapping=not missing)
            if not missing:
                a.mapping = OpticalClock(10000, 0, 1)
            results = [a.record(self.packet(i + 1000)) for i in range(30)]
            qualities = [s["quality"] for s in results if s is not None]
            self.assertIn("CLOCK_UNMAPPED" if missing else "CLOCK_UNCERTAINTY", qualities)
            self.assertEqual(a.valid, 0)

    def test_negative_directed_plateau_and_duplicate_samples(self):
        a = self.acquisition()
        a.plan.update(end_deg=-900, plateau_start_deg=-180, plateau_end_deg=-900)
        valid = []
        for i in range(30):
            p = self.packet(i, angle=-180 - 30 * i * .005, truth=-175 - 30 * i * .005, velocity=-30)
            s = a.record(p)
            self.assertIsNone(a.record(p))
            if s is not None and s["quality"] == "VALID":
                valid.append(s)
        self.assertGreater(len(valid), 3)
        self.assertAlmostEqual(valid[-1]["estimated_velocity_deg_s"], -30)


class IntegratedTests(unittest.TestCase):
    def test_three_turn_scan_streams_samples_and_distinct_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            s = RotationSession(simulate=True, directory=folder)
            try:
                for _ in range(10000):
                    value = s.step()
                    if value["phase"] in ("COMPLETED", "FAULT", "UNKNOWN"):
                        break
                self.assertEqual(value["phase"], "COMPLETED", s.host.detail)
                metrics = s.metrics()
                self.assertGreater(metrics["valid_samples"], 1100)
                self.assertGreater(metrics["estimated_plateau_coverage_deg"], 710)
                self.assertAlmostEqual(metrics["mean_true_optical_speed_deg_s"], 30, delta=.01)
                self.assertAlmostEqual(metrics["mean_estimated_minus_optical_angle_deg"], -5, delta=.01)
                self.assertEqual(metrics["reconstruction"], "PENDING")
                self.assertLessEqual(len(s.history), 1000)
                s.finish()
                with s.logger.sample_path.open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual({r["mode"] for r in rows}, {"SIMULATED"})
                self.assertTrue(any(r["quality"] != "VALID" for r in rows))
                self.assertTrue(all(r["count_low"] and r["count_high"] for r in rows if r["quality"] == "VALID"))
                manifest = json.loads(s.logger.manifest_path.read_text())
                self.assertFalse(manifest["measured_hardware"])
                self.assertEqual(manifest["phase"], "COMPLETED")
            finally:
                s.finish()

    def test_session_requires_explicit_simulation_and_cli_rejects_implicit(self):
        with self.assertRaises(ValueError):
            RotationSession(simulate=False)
        p = subprocess.run([sys.executable, "-B", "-m", "polarimeter_rotation_session"],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 2)

    def test_worker_stop_request_is_not_lost_before_session_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = RotationWorker(folder)
            worker.start_scan()
            worker.stop_requested.set()
            worker.start()
            deadline = time.monotonic() + 5
            value = None
            try:
                while time.monotonic() < deadline:
                    try:
                        value = worker.snapshots.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if value["phase"] in ("CANCELLED", "UNKNOWN"):
                        break
                self.assertIsNotNone(value)
                self.assertEqual(value["phase"], "CANCELLED")
            finally:
                worker.shutdown_requested.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

    def test_worker_disk_failure_requests_stop_and_records_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = RotationWorker(folder)
            with patch("polarimeter_optical.RotationLog.write", side_effect=OSError("fixture disk full")):
                worker.start_scan()
                worker.start()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            value = worker.snapshots.get_nowait()
            self.assertEqual(value["phase"], "UNKNOWN")
            manifests = list(Path(folder).rglob("*-session.json"))
            self.assertEqual(len(manifests), 1)
            record = json.loads(manifests[0].read_text())
            self.assertEqual(record["phase"], "UNKNOWN")
            self.assertIn("disk full", record["detail"])


class HarnessReviewTests(unittest.TestCase):
    def test_terminal_events_are_per_motion_and_all_replies_are_final(self):
        s = Scenario()
        s.start(target=10)
        for _ in range(2000):
            r = s.tick()
            if r["completed"]:
                break
        self.assertEqual(r["terminal"]["outcome"], "COMPLETED")
        self.assertEqual(r["terminal"]["request_id"], "5")
        r = s.tick([s.message("STOP")])
        self.assertFalse(r["completed"])
        self.assertIsNone(r["terminal"])
        self.assertTrue(all(a["state"] == r["state"] and a["output_active"] == r["output_active"]
                            and a["snapshot_scope"] == "FINAL_HARNESS_CYCLE" for a in r["replies"]))

    def test_near_edge_outward_start_is_inhibited_before_duty(self):
        s = Scenario()
        s.controller.config = {**s.controller.config, "max_angle_deg": .5}
        s.tick([s.message("ARM", config_id=s.config["config_id"], challenge=s.controller.challenge[0])])
        result = s.tick([s.message("MOVE_TO", arm_generation=str(s.controller.arm_generation),
            target_deg=.5, speed_deg_s=30, acceleration_deg_s2=60, duration_us="12000000")])
        self.assertEqual(result["state"], "FAULT")
        self.assertEqual(result["duty"], 0)
        self.assertEqual(result["terminal"]["outcome"], "FAULT")
        self.assertTrue(all(r["state"] == "FAULT" for r in result["replies"]))

    def test_actual_overspeed_is_observed_not_assumed_bounded_by_command(self):
        s = Scenario()
        s.start()
        s.plant.gearbox_velocity_deg_s = 300  # Deliberate simulated disturbance.
        for _ in range(10):
            r = s.tick()
            if r["state"] == "FAULT":
                break
        self.assertIn("OVERSPEED", s.controller.faults)
        self.assertEqual(r["duty"], 0)

    def test_underestimated_braking_can_cross_limit_during_coast(self):
        s = Scenario()
        s.start()
        for _ in range(200):
            s.tick()
        edge = s.controller.feedback.angle_deg + 1
        s.controller.config = {**s.controller.config, "max_angle_deg": edge}
        s.harness.loop = replace(s.harness.loop, braking_deceleration_deg_s2=1e9, travel_margin_deg=0)
        s.tick([s.message("STOP")])
        for _ in range(50):
            r = s.tick()
        self.assertGreater(s.plant.qwp_mechanical_deg, edge)
        self.assertIn("TRAVEL_LIMIT", s.controller.faults)
        self.assertEqual(r["duty"], 0)

    def test_repeated_crossings_reset_reversal_dwell(self):
        from polarimeter_simulation import PositionTrajectory
        t = PositionTrajectory(target=-1, speed=10, acceleration=20, initial_speed=0,
            velocity_band=.1, reversal_dwell_us=100000, position_band=.01, position_gain=2)
        t._direction = 1
        t.step(0, 0, .01, 0)
        t.target = 1
        t.step(0, 0, .01, 90000)
        t.target = -1
        t.command = 0
        self.assertEqual(t.step(0, 0, .01, 100000), 0)
        self.assertEqual(t._stationary_since, 100000)
