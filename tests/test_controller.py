"""Proposed controller tests with in-memory frames/time/output, never hardware."""
from dataclasses import FrozenInstanceError
import itertools
import subprocess
import sys
import unittest
import zlib

from polarimeter_controller import (OfflineController, EncoderSample, Readiness,
                                   SimulatedReference, SimulatedOutput, Timing, U64, decimal)
from polarimeter_protocol import (FrameDecoder, FrameError, FrameMessage,
                                  encode_frame)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.ids = itertools.count(100)
        self.controller = OfflineController(
            readiness=Readiness(stationary_band_deg_s=.05, stationary_dwell_us=0, max_count_rate_s=1e9),
            timing=Timing(lease_us=100, request_us=100, feedback_us=100,
                          cycle_us=20, motion_us=1000),
            boot_id="1" * 32, id_factory=lambda: f"{next(self.ids):032x}")
        self.now = 0
        self.rid = 0
        self.config = {"config_id": "2" * 32, "calibration_id": "3" * 32,
                       "counts_per_qwp_rev": 100, "max_speed_deg_s": 10,
                       "max_acceleration_deg_s2": 20, "min_angle_deg": -360,
                       "max_angle_deg": 720}  # All values are SIMULATED fixtures.

    def feedback(self, *, reference=True, time=None, **overrides):
        count = overrides.get("count", round(overrides.get("angle_deg", 0) * self.config["counts_per_qwp_rev"] / 360))
        if overrides.get("velocity_deg_s", 0):
            self.now += 1
            count += 1
        return EncoderSample(self.now if time is None else time, count,
                             "a" * 32 if reference else "b" * 32)

    def reference(self, zero=0):
        ref = SimulatedReference(f"{next(self.ids):032x}", self.controller.config["calibration_id"],
                                 self.controller.feedback.count, zero, 1)
        self.controller.establish_reference(ref)

    def message(self, kind, **fields):
        self.rid += 1
        m = {"v": 1, "mode": "SIMULATED", "type": kind, "request_id": str(self.rid),
             "boot_id": self.controller.boot_id, "session_id": self.controller.session_id}
        if kind == "HELLO":
            m.update(boot_id=None, session_id=None, host_nonce="4" * 32)
        elif kind not in ("STOP", "GET_STATUS"):
            m["not_after_us"] = str(self.now + 80)
        m.update(fields)
        return FrameMessage(encode_frame(m))

    def run_cycle(self, *events, feedback=True, **options):
        f = self.feedback() if feedback is True else feedback if feedback is not False else None
        return self.controller.cycle(events, now_us=self.now, feedback=f, **options)

    def connect(self):
        response = self.run_cycle(self.message("HELLO"))
        self.assertEqual(response[0]["outcome"], "HELLO_REPLY")

    def configure(self):
        self.connect()
        self.now += 1
        self.run_cycle()
        self.now += 1
        self.run_cycle()
        result = self.run_cycle(self.message("CONFIGURE", config_generation="0", config=self.config))
        self.assertEqual(result[0]["outcome"], "ACCEPTED")
        self.reference()
        self.run_cycle(self.message("GET_STATUS"))
        self.assertIsNotNone(self.controller.challenge)

    def arm(self):
        self.configure()
        result = self.run_cycle(self.message("ARM", config_id=self.config["config_id"],
                                             challenge=self.controller.challenge[0]))
        self.assertEqual(result[0]["outcome"], "ACCEPTED")
        self.assertEqual(self.controller.state, "ARMED")

    def move(self, **overrides):
        fields = dict(arm_generation=str(self.controller.arm_generation), target_deg=90,
                      speed_deg_s=5, acceleration_deg_s2=10, duration_us="500")
        fields.update(overrides)
        return self.message("MOVE_TO", **fields)

    def heartbeat(self, seq, deadline):
        return self.message("HEARTBEAT", request_id=str(seq), heartbeat_seq=str(seq),
                            arm_generation=str(self.controller.arm_generation),
                            not_after_us=str(deadline))

    def test_bootstrap_unknown_feedback_and_atomic_configuration(self):
        self.assertFalse(self.controller.output.active)
        self.assertIsNone(self.controller.snapshot()["angle_deg"])
        self.connect()
        request = self.message("CONFIGURE", config_generation="0", config=self.config)
        self.assertEqual(self.run_cycle(request, feedback=False)[0]["outcome"], "REJECTED")
        self.assertIsNone(self.controller.config)
        self.now = 1
        self.run_cycle()
        self.now = 2
        self.run_cycle()
        configured = self.message("CONFIGURE", config_generation="0", config=self.config)
        self.run_cycle(configured)
        self.assertEqual(self.controller.state, "CONFIGURED")
        self.assertIsNone(self.controller.challenge)
        self.run_cycle(self.message("GET_STATUS"))
        self.assertIsNone(self.controller.challenge)
        self.reference()
        self.run_cycle(self.message("GET_STATUS"))
        self.assertIsNotNone(self.controller.challenge)

    def test_stop_before_hello_and_stale_identity_needs_no_arm(self):
        request = self.message("STOP", boot_id="9" * 32, session_id=None)
        for _ in range(2):
            count = self.controller.output.inhibit_count
            result = self.run_cycle(request)
            self.assertGreater(self.controller.output.inhibit_count, count)
            self.assertEqual(result[0]["outcome"], "INHIBIT_REQUESTED")
            self.assertEqual(self.controller.state, "STOPPED")

    def test_move_then_stop_has_no_transient_enable(self):
        self.arm()
        self.run_cycle(self.move(), self.message("STOP"))
        self.assertEqual(self.controller.output.enable_count, 0)
        self.assertFalse(self.controller.output.active)
        self.assertEqual(self.controller.state, "STOPPED")

    def test_stop_then_arm_suppresses_rearming(self):
        self.configure()
        arm = self.message("ARM", config_id=self.config["config_id"], challenge=self.controller.challenge[0])
        result = self.run_cycle(self.message("STOP"), arm)
        self.assertEqual(result[1]["outcome"], "BATCH_INHIBITED")
        self.assertIsNone(self.controller.challenge)
        self.assertIsNone(self.controller.lease_end)

    def test_fault_stop_permutations_preserve_fault_and_prevent_motion(self):
        for reverse in (False, True):
            self.setUp()
            self.arm()
            events = [FrameError("fixture damage"), self.message("STOP")]
            self.run_cycle(*(events[::-1] if reverse else events))
            self.assertEqual(self.controller.state, "FAULT")
            self.assertIn("PROTOCOL", self.controller.faults)
            self.assertEqual(self.controller.output.enable_count, 0)
            self.run_cycle(self.message("STOP"))
            self.assertIn("PROTOCOL", self.controller.faults)

    def test_hello_during_motion_revokes_and_suppresses_batch_move(self):
        self.arm()
        self.run_cycle(self.move())
        old_session = self.controller.session_id
        self.run_cycle(self.message("HELLO"), self.move())
        self.assertFalse(self.controller.output.active)
        self.assertNotEqual(self.controller.session_id, old_session)
        self.assertIsNone(self.controller.lease_end)
        self.assertIsNone(self.controller.feedback)
        self.assertEqual(self.controller.state, "DISARMED")

    def test_lease_cannot_revive_at_equality_and_heartbeat_cannot_extend_motion(self):
        self.arm()
        for t in (20, 40, 60, 80):
            self.now = t
            self.run_cycle()
        heartbeat = self.heartbeat(1, 180)
        self.now = 102
        self.run_cycle(heartbeat)
        self.assertIn("LEASE_EXPIRED", self.controller.faults)
        self.assertIsNone(self.controller.lease_end)
        self.setUp()
        self.arm()
        self.run_cycle(self.move(duration_us="40"))
        self.now = 20
        self.run_cycle(self.heartbeat(1, 120))
        self.now = 42
        self.run_cycle()
        self.assertIn("MOTION_DURATION", self.controller.faults)
        self.assertFalse(self.controller.output.active)

    def test_delayed_new_heartbeat_shortens_and_replay_does_not_renew(self):
        self.arm()
        self.now = 10
        packet = self.heartbeat(1, 60)
        self.run_cycle(packet)
        self.assertEqual(self.controller.lease_end, 60)
        self.now = 20
        self.run_cycle(packet)
        self.assertEqual(self.controller.lease_end, 60)
        self.run_cycle(self.heartbeat(2, 119))
        self.assertEqual(self.controller.lease_end, 119)
        self.run_cycle(self.heartbeat(3, 121))
        self.assertEqual(self.controller.lease_end, 119)
        self.run_cycle(self.heartbeat(4, 20))
        self.assertEqual(self.controller.lease_end, 119)

    def test_same_tick_is_valid_but_regression_large_gap_and_overflow_fault(self):
        self.arm()
        self.run_cycle()
        self.assertEqual(self.controller.state, "ARMED")
        self.now += 21
        self.run_cycle()
        self.assertIn("CONTROL_DEADLINE", self.controller.faults)
        self.setUp()
        self.now = 10
        self.run_cycle()
        self.now = 9
        self.run_cycle()
        self.assertIn("CLOCK", self.controller.faults)
        self.setUp()
        self.now = U64 - 99
        self.run_cycle(feedback=False)
        self.assertIn("TIME_OVERFLOW", self.controller.faults)

    def test_feedback_age_equality_and_reference_loss_fault(self):
        for missing_reference in (False, True):
            self.setUp()
            self.arm()
            if missing_reference:
                self.run_cycle(feedback=self.feedback(reference=False))
            else:
                # Timing fixture adjusted to isolate the feedback timer.
                self.controller.timing = Timing(lease_us=200, request_us=100, feedback_us=10,
                                               cycle_us=20, motion_us=1000)
                self.now += 10
                self.run_cycle(feedback=False)
            self.assertIn("FEEDBACK", self.controller.faults)
            self.assertIsNone(self.controller.snapshot()["angle_deg"])

    def test_exact_retry_does_not_repeat_motion_and_byte_conflict_faults(self):
        self.arm()
        request = self.move()
        self.run_cycle(request)
        self.run_cycle(request)
        self.assertEqual(self.controller.output.enable_count, 1)
        # Semantically equal JSON with different whitespace has different identity.
        payload = request.payload.replace(b',', b', ')
        covered = b'QWP1 ' + str(len(payload)).encode() + b' ' + payload
        conflict = FrameMessage(covered + f' {zlib.crc32(covered):08X}\n'.encode())
        self.assertEqual(request.message, conflict.message)
        self.run_cycle(conflict)
        self.assertIn("ID_CONFLICT", self.controller.faults)
        self.assertFalse(self.controller.output.active)

    def test_same_batch_conflict_is_detected_before_output_enable(self):
        self.arm()
        move = self.move()
        collision = self.message("GET_STATUS", request_id=move.message["request_id"])
        self.run_cycle(move, collision)
        self.assertIn("ID_CONFLICT", self.controller.faults)
        self.assertEqual(self.controller.output.enable_count, 0)

    def test_cache_eviction_rejects_stale_id(self):
        self.connect()
        old = self.message("GET_STATUS")
        self.run_cycle(old)
        for _ in range(9):
            self.run_cycle(self.message("GET_STATUS"))
        self.assertEqual(len(self.controller._cache), 8)
        self.assertEqual(self.run_cycle(old)[0]["outcome"], "STALE_ID")

    def test_tx_rx_overflow_preflight_inhibits_without_enabling(self):
        for options in ({"tx_capacity": 0}, {"rx_overflow": True}):
            self.setUp()
            self.arm()
            self.run_cycle(self.move(), **options)
            self.assertEqual(self.controller.state, "FAULT")
            self.assertEqual(self.controller.output.enable_count, 0)
        self.setUp()
        self.arm()
        self.run_cycle(*([self.message("GET_STATUS")] * 9))
        self.assertIn("RX_OVERFLOW", self.controller.faults)

    def test_parser_timeout_in_silence_faults_model_even_if_stop_is_swallowed(self):
        self.arm()
        parser = FrameDecoder(frame_timeout_s=.00001, preserve_payload=True)
        self.run_cycle(*parser.feed(b'QWP1 99 {', now=0))
        self.now = 10
        self.run_cycle(*parser.feed(b'', now=.00001))
        self.assertIn("PROTOCOL", self.controller.faults)
        self.assertFalse(self.controller.output.active)
        self.assertEqual(parser.feed(self.message("STOP").frame, now=.00001), ())

    def test_boot_identity_reset_rejects_previous_commands(self):
        self.arm()
        old = self.move()
        self.controller = OfflineController(timing=self.controller.timing, boot_id="8" * 32,
                                            readiness=self.controller.readiness,
                                            output=self.controller.output)
        result = self.run_cycle(old)
        self.assertEqual(result[0]["outcome"], "WRONG_SESSION")
        self.assertFalse(self.controller.output.active)

    def test_challenge_expiry_stop_and_configuration_generations(self):
        self.configure()
        challenge = self.controller.challenge[0]
        # Keep coherent samples flowing: isolate challenge expiry from the
        # independent encoder-gap fault at the same timestamp boundary.
        while self.now < 102:
            self.now = min(102, self.now + 10)
            self.run_cycle()
        arm = self.message("ARM", config_id=self.config["config_id"], challenge=challenge)
        self.assertEqual(self.run_cycle(arm)[0]["outcome"], "REJECTED")
        self.assertIsNone(self.controller.challenge)
        self.run_cycle(self.message("GET_STATUS"))
        old_challenge = self.controller.challenge[0]
        self.run_cycle(self.message("STOP"))
        self.assertEqual(self.run_cycle(self.message("ARM", config_id=self.config["config_id"],
                                                    challenge=old_challenge))[0]["outcome"], "REJECTED")
        previous = dict(self.controller.config)
        self.run_cycle(self.message("CONFIGURE", config_generation="0", config={**self.config, "max_speed_deg_s": 9}))
        self.assertEqual(self.controller.config, previous)

    def test_strict_schema_boolean_and_canonical_number_validation(self):
        self.arm()
        malformed = self.move(speed_deg_s=True)
        self.run_cycle(malformed)
        self.assertIn("PROTOCOL", self.controller.faults)
        for value in (True, 1, '-1', '01', '+1', '1.0', str(U64 + 1), ''):
            with self.assertRaises(ValueError):
                decimal(value)
        with self.assertRaises(ValueError):
            Timing(lease_us=True, request_us=1, feedback_us=1, cycle_us=1, motion_us=1)
        with self.assertRaises(TypeError):
            OfflineController(timing=self.controller.timing, readiness=self.controller.readiness,
                              boot_id="1" * 32, output=object())

    def test_raw_frame_and_payload_are_immutable_preserved_and_defensive(self):
        event = self.message("STOP")
        with self.assertRaises(FrozenInstanceError):
            event.frame = b'changed'
        changed = event.message
        changed["type"] = "MOVE_TO"
        self.assertEqual(event.message["type"], "STOP")
        parser = FrameDecoder(frame_timeout_s=1, preserve_payload=True)
        result = parser.feed(event.frame, now=0)[0]
        self.assertEqual(result.payload, event.payload)
        self.assertEqual(result.frame, event.frame)

    def test_controller_import_does_not_load_serial_or_heavy_dependencies(self):
        code = ("import sys; import polarimeter_controller; "
                "assert not {'serial','numpy','scipy','matplotlib'} & set(sys.modules)")
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stop_and_status_cannot_issue_a_new_challenge_in_same_cycle(self):
        self.configure()
        self.run_cycle(self.message("STOP"), self.message("GET_STATUS"))
        self.assertIsNone(self.controller.challenge)
        self.assertIsNone(self.controller.lease_end)

    def test_disarm_precedence_and_hello_nonce_echo(self):
        response = self.run_cycle(self.message("HELLO"))
        self.assertEqual(response[0]["host_nonce"], "4" * 32)
        self.setUp()
        self.arm()
        result = self.run_cycle(self.move(), self.message("DISARM"))
        self.assertEqual(self.controller.output.enable_count, 0)
        self.assertEqual(self.controller.state, "DISARMED")
        self.assertEqual(result[1]["outcome"], "ACCEPTED")

    def test_simultaneous_timer_faults_are_all_latched(self):
        self.arm()
        self.run_cycle(self.move(duration_us="100"))
        self.now = 102
        self.run_cycle(feedback=False)
        self.assertTrue({"CONTROL_DEADLINE", "LEASE_EXPIRED", "FEEDBACK", "MOTION_DURATION"}
                        <= self.controller.faults)

    def test_fault_clear_requires_fresh_stationary_evidence_and_never_arms(self):
        self.arm()
        self.run_cycle(FrameError("fixture"))
        result = self.run_cycle(self.message("CLEAR_FAULT"),
                                feedback=self.feedback(velocity_deg_s=1))
        self.assertEqual(result[0]["outcome"], "REJECTED")
        self.now += 1
        self.run_cycle()
        self.now += 1
        self.run_cycle()
        self.run_cycle(self.message("GET_STATUS"))
        result = self.run_cycle(self.message("CLEAR_FAULT"))
        self.assertEqual(result[0]["outcome"], "ACCEPTED")
        self.assertEqual(self.controller.state, "DISARMED")
        self.assertFalse(self.controller.output.active)
        self.assertIsNone(self.controller.challenge)

    def test_configuration_change_cannot_reuse_old_scaling_calibration(self):
        self.configure()
        old = dict(self.controller.config)
        candidate = {**self.config, "counts_per_qwp_rev": 200}
        response = self.run_cycle(self.message("CONFIGURE", config_generation="1", config=candidate))
        self.assertEqual(response[0]["outcome"], "REJECTED")
        self.assertEqual(self.controller.config, old)
        candidate["calibration_id"] = "5" * 32
        response = self.run_cycle(self.message("CONFIGURE", config_generation="1", config=candidate))
        self.assertEqual(response[0]["outcome"], "ACCEPTED")
        self.assertIsNone(self.controller.challenge)

    def test_identity_failure_and_generation_exhaustion_cannot_arm(self):
        self.connect()
        self.controller.id_factory = lambda: self.controller.boot_id
        self.run_cycle(self.message("HELLO"))
        self.assertIn("IDENTITY", self.controller.faults)
        self.assertFalse(self.controller.output.active)
        self.setUp()
        self.configure()
        self.controller.arm_generation = U64
        self.run_cycle(self.message("ARM", config_id=self.config["config_id"],
                                    challenge=self.controller.challenge[0]))
        self.assertIn("COUNTER_OVERFLOW", self.controller.faults)
        self.assertFalse(self.controller.output.active)

    def test_real_mode_unknown_fields_and_overspeed_do_not_enable(self):
        self.arm()
        result = self.run_cycle(self.move(speed_deg_s=11))
        self.assertEqual(result[0]["outcome"], "REJECTED")
        self.assertEqual(self.controller.output.enable_count, 0)
        self.run_cycle(self.move(mode="HARDWARE"))
        self.assertIn("PROTOCOL", self.controller.faults)
        self.assertEqual(self.controller.output.enable_count, 0)
        self.setUp()
        self.arm()
        self.run_cycle(self.message("STOP", extra=True))
        self.assertIn("PROTOCOL", self.controller.faults)

    def test_conflicting_heartbeat_is_detected_before_a_move(self):
        self.arm()
        self.run_cycle(self.heartbeat(1, 90))
        changed = self.heartbeat(1, 95)
        self.run_cycle(self.move(), changed)
        self.assertIn("ID_CONFLICT", self.controller.faults)
        self.assertEqual(self.controller.output.enable_count, 0)

    def test_external_inhibit_and_stop_latch_fault_and_suppress_all_enables(self):
        self.arm()
        self.run_cycle(self.move(), self.message("STOP"), external_inhibit=True)
        self.assertIn("EXTERNAL_INHIBIT", self.controller.faults)
        self.assertEqual(self.controller.state, "FAULT")
        self.assertEqual(self.controller.output.enable_count, 0)
        self.run_cycle(self.message("CLEAR_FAULT"), external_inhibit=True)
        self.assertEqual(self.controller.state, "FAULT")

    def test_equal_sample_time_is_not_a_discontinuity_but_backwards_is(self):
        self.arm()
        sample = self.feedback()
        self.now = 10
        self.run_cycle(feedback=sample)
        self.assertEqual(self.controller.state, "ARMED")
        self.now = 11
        self.run_cycle(feedback=sample)
        self.assertEqual(self.controller.state, "ARMED")
        self.run_cycle(feedback=self.feedback(time=10))
        self.now = 12
        self.run_cycle(feedback=self.feedback(time=9))
        self.assertIn("FEEDBACK", self.controller.faults)
        self.assertFalse(self.controller.output.active)

    def test_motion_time_and_travel_are_bounded_independently_of_heartbeat(self):
        self.arm()
        result = self.run_cycle(self.move(duration_us="1001"))
        self.assertEqual(result[0]["outcome"], "REJECTED")
        result = self.run_cycle(self.move(target_deg=721))
        self.assertEqual(result[0]["outcome"], "REJECTED")
        self.assertEqual(self.controller.output.enable_count, 0)
        self.run_cycle(self.move())
        self.now = 10
        self.run_cycle(self.heartbeat(1, 110), feedback=self.feedback(angle_deg=725))
        self.assertIn("TRAVEL_LIMIT", self.controller.faults)
        self.assertFalse(self.controller.output.active)

    def test_out_of_travel_reference_cannot_issue_challenge_or_arm(self):
        self.configure()
        old_challenge = self.controller.challenge[0]
        self.reference(zero=800)
        self.run_cycle(self.message("GET_STATUS"))
        self.assertIsNone(self.controller.challenge)
        result = self.run_cycle(self.message("ARM", config_id=self.config["config_id"], challenge=old_challenge))
        self.assertNotEqual(result[0]["outcome"], "ACCEPTED")
        self.assertEqual(self.controller.output.enable_count, 0)

    def test_stale_unarmed_reference_never_returns_without_explicit_epoch(self):
        self.configure()
        old_epoch = self.controller._reference.epoch
        self.now += self.controller.timing.feedback_us
        self.run_cycle(feedback=False)
        self.assertIsNone(self.controller.challenge)
        self.assertIsNone(self.controller._reference)
        self.now += 1
        self.run_cycle(self.message("GET_STATUS"))
        self.assertIsNone(self.controller.challenge)
        self.assertIn("FEEDBACK", self.controller.faults)
        self.assertIsNone(self.controller._reference)
        self.now += 1
        self.run_cycle()  # Rebuild coherent velocity/stationary evidence.
        self.reference()
        self.assertNotEqual(self.controller._reference.epoch, old_epoch)
        self.assertEqual(self.run_cycle(self.message("CLEAR_FAULT"))[0]["outcome"], "ACCEPTED")
        self.assertEqual(self.run_cycle(self.message("CONFIGURE", config_generation=str(self.controller.config_generation),
            config=self.config))[0]["outcome"], "ACCEPTED")
        self.run_cycle(self.message("GET_STATUS"))
        self.assertIsNotNone(self.controller.challenge)

    def test_contradictory_same_timestamp_invalidates_reference(self):
        self.configure()
        self.run_cycle(feedback=EncoderSample(self.now, 1, "a" * 32))
        self.assertIn("FEEDBACK_INTEGRITY", self.controller.faults)
        self.assertIsNone(self.controller._reference)
        self.now += 1
        self.run_cycle()
        self.now += 1
        self.run_cycle()
        result = self.run_cycle(self.message("CLEAR_FAULT"))
        self.assertEqual(result[0]["outcome"], "REJECTED")

    def test_angle_and_velocity_are_derived_from_count_epoch(self):
        self.configure()
        self.now += 10
        self.run_cycle(feedback=EncoderSample(self.now, 1, "a" * 32))
        self.assertAlmostEqual(self.controller.feedback.angle_deg, 3.6)
        self.assertAlmostEqual(self.controller.feedback.velocity_deg_s, 360000)
        self.assertEqual(self.controller.feedback.count, 1)

    def test_travel_fault_does_not_clear_with_fresh_outside_sample(self):
        self.arm()
        self.now += 10
        self.run_cycle(feedback=EncoderSample(self.now, 201, "a" * 32))
        self.assertIn("TRAVEL_LIMIT", self.controller.faults)
        self.now += 1
        self.run_cycle(feedback=EncoderSample(self.now, 201, "a" * 32))
        result = self.run_cycle(self.message("CLEAR_FAULT"), feedback=EncoderSample(self.now, 201, "a" * 32))
        self.assertNotEqual(result[0]["outcome"], "ACCEPTED")
        with self.assertRaises(ValueError):
            self.reference(zero=0)  # A coordinate change cannot clear a travel concern.
        self.now += 10
        self.run_cycle(feedback=EncoderSample(self.now, 0, "a" * 32))
        self.now += 1
        self.run_cycle()
        result = self.run_cycle(self.message("CLEAR_FAULT"))
        self.assertEqual(result[0]["outcome"], "ACCEPTED")

    def test_protocol_and_id_conflict_recovery_have_distinct_predicates(self):
        self.arm()
        self.run_cycle(FrameError("fixture"))
        self.assertEqual(self.run_cycle(self.message("CLEAR_FAULT"))[0]["outcome"], "REJECTED")
        self.run_cycle(self.message("GET_STATUS"))
        self.assertEqual(self.run_cycle(self.message("CLEAR_FAULT"))[0]["outcome"], "ACCEPTED")
        self.setUp()
        self.arm()
        req = self.move()
        other = self.message("GET_STATUS", request_id=req.message["request_id"])
        self.run_cycle(req, other)
        self.run_cycle(self.message("GET_STATUS"))
        self.assertEqual(self.run_cycle(self.message("CLEAR_FAULT"))[0]["outcome"], "REJECTED")
        old_session = self.controller.session_id
        self.run_cycle(self.message("HELLO"))
        self.assertNotEqual(self.controller.session_id, old_session)
        self.now += 1
        self.run_cycle()
        self.now += 1
        self.run_cycle()
        self.reference()
        self.assertEqual(self.run_cycle(self.message("CLEAR_FAULT"))[0]["outcome"], "ACCEPTED")

    def test_request_and_heartbeat_terminal_ids_force_session_renewal(self):
        for heartbeat in (False, True):
            self.setUp()
            self.arm()
            packet = self.heartbeat(U64, self.now + 80) if heartbeat else self.message("GET_STATUS", request_id=str(U64))
            self.run_cycle(packet)
            self.assertIn("SEQUENCE_EXHAUSTED", self.controller.faults)
            self.assertFalse(self.controller.output.active)
            result = self.run_cycle(self.message("CLEAR_FAULT"))
            self.assertEqual(result[0]["outcome"], "REJECTED")
            self.run_cycle(self.message("HELLO"))
            self.assertFalse(self.controller._session_exhausted)
            self.assertEqual(self.controller.state, "FAULT")

    def test_stale_arm_heartbeat_conflict_is_rejected_without_fault(self):
        self.arm()
        stale = self.heartbeat(1, self.now + 80)
        self.run_cycle(stale)
        self.run_cycle(self.message("STOP"))
        self.run_cycle(self.message("GET_STATUS"))
        self.run_cycle(self.message("ARM", config_id=self.config["config_id"], challenge=self.controller.challenge[0]))
        self.run_cycle(self.heartbeat(1, self.now + 90))
        deadline = self.controller.lease_end
        self.run_cycle(stale)
        self.assertEqual(self.controller.faults, set())
        self.assertEqual(self.controller.lease_end, deadline)

    def test_all_batch_snapshots_report_final_state(self):
        self.arm()
        result = self.run_cycle(self.message("GET_STATUS"), self.message("STOP"))
        self.assertTrue(all(r["state"] == "STOPPED" and r["snapshot_scope"] == "FINAL_CYCLE" for r in result))

    def test_stationary_dwell_is_explicit_and_uses_sample_time(self):
        self.configure()
        self.controller.readiness = Readiness(.1, 5, 1e9)
        packet = self.message("ARM", config_id=self.config["config_id"], challenge=self.controller.challenge[0])
        self.assertEqual(self.run_cycle(packet)[0]["outcome"], "REJECTED")
        for _ in range(5):
            self.run_cycle()  # Same sample timestamp cannot satisfy the dwell.
        self.now += 5
        packet = self.message("ARM", config_id=self.config["config_id"], challenge=self.controller.challenge[0])
        self.assertEqual(self.run_cycle(packet)[0]["outcome"], "ACCEPTED")
