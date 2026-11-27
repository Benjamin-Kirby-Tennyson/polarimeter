import csv
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polarimeter_core import JonesModel, ScanController, target_angle


class OpticsTests(unittest.TestCase):
    def test_horizontal_and_vertical_analytic_curves(self):
        for angle in range(361):
            cross = .5 * math.sin(2 * math.radians(angle))**2
            self.assertAlmostEqual(JonesModel().intensity(angle), 1 - cross)
            self.assertAlmostEqual(JonesModel(ex=0, ey=1).intensity(angle), cross)

    def test_opposite_circular_inputs_have_two_theta_modulation(self):
        for sign in (-1, 1):
            model = JonesModel(ex=1 / math.sqrt(2), ey=sign * 1j / math.sqrt(2))
            for angle in range(361):
                expected = .5 * (1 + sign * math.sin(2 * math.radians(angle)))
                self.assertAlmostEqual(model.intensity(angle), expected)

    def test_lossless_retarder_and_passive_analyzer(self):
        for retardance in (0, math.pi / 2, math.pi, .7):
            model = JonesModel(ex=.3 + .5j, ey=.2 - .6j,
                               analyzer_deg=31, retardance_rad=retardance)
            incident = abs(model.ex)**2 + abs(model.ey)**2
            for angle in range(0, 361, 3):
                x, y = model.field(angle)
                self.assertAlmostEqual(abs(x)**2 + abs(y)**2, incident)
                self.assertGreaterEqual(model.intensity(angle), 0)
                self.assertLessEqual(model.intensity(angle), incident + 1e-12)
                self.assertAlmostEqual(model.intensity(angle), model.intensity(angle + 180))

    def test_zero_retardance_is_malus_law_and_no_analyzer_would_be_constant(self):
        for angle in range(0, 361, 5):
            model = JonesModel(analyzer_deg=30, retardance_rad=0)
            self.assertAlmostEqual(model.intensity(angle), .75)
            x, y = JonesModel().field(angle)
            self.assertAlmostEqual(abs(x)**2 + abs(y)**2, 1)

    def test_invalid_physics_and_targets_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                JonesModel(ex=complex(value, 0))
            with self.assertRaises(ValueError):
                JonesModel(analyzer_deg=value)
            with self.assertRaises(ValueError):
                JonesModel().intensity(value)
        for value in ("", "abc", -1, 361, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                target_angle(value)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ScanController(simulate=True, directory=self.temp.name)
        self.addCleanup(self.controller.stop)

    def rows(self, path):
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def test_full_scan_angle_provenance_logging_and_completion(self):
        c = self.controller
        c.start()
        points = []
        while c.running:
            points.append(c.tick())
        self.assertEqual(len(points), 181)
        self.assertEqual([p[0] for p in points], list(range(0, 361, 2)))
        self.assertIsNone(c.log)
        rows = self.rows(c.last_path)
        self.assertEqual(len(rows), 181)
        self.assertEqual(c.last_path.parent.name, "simulated")
        self.assertTrue(c.last_path.name.startswith("simulated-qwp-"))
        self.assertEqual({r["mode"] for r in rows}, {"SIMULATED"})
        self.assertEqual({r["angle_source"] for r in rows}, {"SIMULATED_MODEL_ANGLE"})
        self.assertEqual({r["detector_source"] for r in rows}, {"SIMULATED_JONES"})
        self.assertEqual({r["intensity_unit"] for r in rows}, {"a.u. (uncalibrated)"})
        self.assertTrue(all(r["timestamp_utc"].endswith("+00:00") for r in rows))
        elapsed = [float(r["elapsed_s"]) for r in rows]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertEqual(float(rows[0]["input_ex_real"]), 1)
        self.assertAlmostEqual(float(rows[0]["retardance_rad"]), math.pi / 2)
        self.assertEqual(c.tick(), None)

    def test_stop_and_new_scan_do_not_overwrite_or_resume(self):
        c = self.controller
        c.start()
        c.tick()
        path = c.last_path
        c.stop()
        self.assertIsNone(c.tick())
        self.assertEqual(len(self.rows(path)), 1)
        c.start()
        self.assertNotEqual(c.last_path, path)
        self.assertEqual(c.tick()[0], 0)
        c.stop()
        self.assertEqual(len(self.rows(path)), 1)

    def test_manual_jog_target_and_bounded_history(self):
        c = self.controller
        self.assertEqual(c.jog(-5)[0], 355)
        self.assertEqual(c.jog(5)[0], 0)
        self.assertEqual(c.move_to(90)[0], 90)
        for angle in range(400):
            c.move_to(angle % 360)
        self.assertEqual(len(c.history), 361)
        c.stop()
        self.assertEqual(len(self.rows(c.last_path)), 403)

    def test_manual_control_rejected_during_scan(self):
        c = self.controller
        c.start()
        with self.assertRaises(RuntimeError):
            c.move_to(45)
        self.assertEqual(c.tick()[0], 0)

    def test_detector_or_disk_failure_stops_and_closes_log(self):
        c = self.controller
        for failure in ("detector", "disk"):
            c.start()
            stream = c.log.file
            obj, attr = (c.detector, "read_intensity") if failure == "detector" else (c.log, "write")
            with patch.object(obj, attr, side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    c.tick()
            self.assertFalse(c.running)
            self.assertTrue(stream.closed)
            self.assertIsNone(c.log)

    def test_unwritable_destination_does_not_start(self):
        path = Path(self.temp.name) / "file"
        path.write_text("existing")
        c = ScanController(simulate=True, directory=path)
        with self.assertRaises(OSError):
            c.start()
        self.assertFalse(c.running)
        self.assertEqual(path.read_text(), "existing")

    def test_hardware_is_locked_at_controller_and_adapter_boundaries(self):
        directory = Path(self.temp.name) / "never-created"
        c = ScanController(directory=directory)
        for action in (c.start, lambda: c.move_to(30), lambda: c.jog(5),
                       lambda: c.motor.move_to(30), c.detector.read_intensity):
            with self.assertRaises(RuntimeError):
                action()
        c.stop()
        self.assertIsNone(c.tick())
        self.assertFalse(directory.exists())
        self.assertEqual(len(c.history), 0)


if __name__ == "__main__":
    unittest.main()
