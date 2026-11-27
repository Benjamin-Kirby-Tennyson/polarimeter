"""Opt-in mapped Tk tests; synthetic events do not verify physical touch."""
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("POLARIMETER_GUI_TESTS") == "1",
                     "set POLARIMETER_GUI_TESTS=1 from a graphical desktop")
class GuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from polarimeter_core import ScanController
        from polarimeter_gui import PolarimeterApp
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.controller = ScanController(simulate=True, directory=self.temp.name)
        self.app = PolarimeterApp(self.root, self.controller, fullscreen=False)
        self.addCleanup(self.app.close)
        self.root.update()

    def find_button(self, text):
        parent = self.app.overlay or self.app.body
        return next(w for w in parent.winfo_children()
                    if w.winfo_class() == "Button" and w.cget("text") == text)

    def assert_inside(self, parent):
        for widget in parent.winfo_children():
            self.assertGreaterEqual(widget.winfo_x(), 0)
            self.assertGreaterEqual(widget.winfo_y(), 0)
            self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), parent.winfo_width())
            self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), parent.winfo_height())
            if widget.winfo_class() == "Button":
                self.assertGreaterEqual(widget.winfo_height(), 44)
            if widget.winfo_class() == "Frame":
                self.assert_inside(widget)

    def test_every_control_and_keypad_fits_640_by_480(self):
        self.assertEqual((self.app.body.winfo_width(), self.app.body.winfo_height()), (640, 480))
        self.assert_inside(self.app.body)
        self.app.target_button.invoke()
        self.root.update()
        self.assert_inside(self.app.body)
        self.find_button("Clear").invoke()
        for digit in "135":
            self.find_button(digit).invoke()
        self.find_button("Set").invoke()
        self.app.go_button.invoke()
        self.assertEqual(self.controller.motor.angle_deg, 135)

    def test_start_stop_cancel_timer_and_flush_csv(self):
        self.app.start_button.invoke()
        self.assertTrue(self.controller.running)
        self.assertEqual(str(self.app.go_button.cget("state")), "disabled")
        self.assertIsNotNone(self.app.tick_id)
        stream = self.controller.log.file
        self.app.stop_button.invoke()
        self.assertFalse(self.controller.running)
        self.assertIsNone(self.app.tick_id)
        self.assertTrue(stream.closed)
        self.assertEqual(str(self.app.go_button.cget("state")), "normal")
        self.root.update()
        self.assertEqual(len(self.controller.history), 1)

    def test_bad_target_and_disk_error_are_visible_and_stop(self):
        self.app.target.set("nan")
        self.app.go_button.invoke()
        self.assertIn("ERROR", self.app.status.cget("text"))
        with patch.object(self.controller, "start", side_effect=OSError("Disk full")):
            self.app.start_button.invoke()
        self.assertIn("Disk full", self.app.status.cget("text"))
        self.assertFalse(self.controller.running)

    def test_pointer_check_fits_and_creates_no_measurements(self):
        self.app.touch_check()
        self.root.update()
        for number in range(1, 6):
            self.root.update()
            self.assert_inside(self.app.body)
            self.find_button(str(number)).invoke()
        self.assertEqual(self.app.touch_index, 5)
        self.assertIn("source unverified", self.app.status.cget("text"))
        self.assertEqual(list(Path(self.temp.name).rglob("*.csv")), [])

    def test_motor_panel_enumerates_without_opening_ports_or_logging(self):
        port = SimpleNamespace(device="/dev/ttyUSB-test", vid=0x10c4,
                               pid=0xea60, serial_number="fixture",
                               location="test-only")
        with patch("serial.tools.list_ports.comports", return_value=[port]) as enumerate_ports, \
                patch("serial.Serial", side_effect=AssertionError("Port opened")) as serial_open:
            self.find_button("Motor / USB").invoke()
            self.root.update()
            self.assert_inside(self.app.body)
            details = next(w for w in self.app.overlay.winfo_children()
                           if w.winfo_class() == "Text")
            self.assertIn("10c4:ea60", details.get("1.0", "end"))
            self.assertIn("/dev/ttyUSB-test", details.get("1.0", "end"))
            self.find_button("Refresh USB").invoke()
            self.assertEqual(enumerate_ports.call_count, 2)
            serial_open.assert_not_called()
        self.assertFalse(self.controller.running)
        self.assertEqual(list(Path(self.temp.name).rglob("*.csv")), [])
        self.find_button("Back").invoke()
        self.assertIsNone(self.app.overlay)

    def test_motor_panel_reports_discovery_failure_and_recovers(self):
        with patch("polarimeter_serial.discover_usb_devices", side_effect=OSError("Access denied")):
            self.app.motor_status()
        details = next(w for w in self.app.overlay.winfo_children()
                       if w.winfo_class() == "Text")
        self.assertIn("Access denied", details.get("1.0", "end"))
        with patch("polarimeter_serial.discover_usb_devices", return_value=()):
            self.find_button("Refresh USB").invoke()
        self.assertIn("No USB serial devices visible", details.get("1.0", "end"))
        self.assertEqual(str(details.cget("state")), "disabled")
        self.assertEqual(list(Path(self.temp.name).rglob("*.csv")), [])

    def test_locked_gui_disables_every_physical_control(self):
        from polarimeter_core import ScanController
        from polarimeter_gui import PolarimeterApp
        self.app.close()
        import tkinter as tk
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.controller = ScanController(directory=self.temp.name)
        self.app = PolarimeterApp(self.root, self.controller, fullscreen=False)
        self.addCleanup(self.app.close)
        self.root.update()
        with patch("polarimeter_serial.discover_usb_devices", return_value=()):
            self.app.motor_status()
        self.find_button("Back").invoke()
        self.assertEqual(str(self.app.rotation_button.cget("state")), "disabled")
        self.app.rotation_lab()
        self.assertIsNone(self.app.overlay)
        for button in (self.app.start_button, self.app.stop_button, *self.app.manual_buttons):
            self.assertEqual(str(button.cget("state")), "disabled")
            button.invoke()
        self.assertEqual(list(Path(self.temp.name).rglob("*.csv")), [])

    def test_rotation_panel_fits_and_cancel_closes_worker_before_back(self):
        self.app.rotation_button.invoke()
        self.root.update()
        self.assert_inside(self.app.body)
        panel = self.app.rotation_panel
        self.assertEqual(list(Path(self.temp.name).rglob("*.csv")), [])
        panel.start_button.invoke()
        panel.stop_button.invoke()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.root.update()
            if panel.last_snapshot is not None and panel.last_snapshot["phase"] == "CANCELLED":
                break
            time.sleep(.01)
        self.assertIsNotNone(panel.last_snapshot)
        self.assertEqual(panel.last_snapshot["phase"], "CANCELLED")
        self.find_button("Back").invoke()
        while time.monotonic() < deadline and self.app.overlay is not None:
            self.root.update()
            time.sleep(.01)
        self.assertIsNone(self.app.overlay)
        self.assertFalse(panel.worker.is_alive())

    def test_rotation_unknown_status_never_displays_old_measurements(self):
        from polarimeter_rotation_session import RotationWorker
        self.app.rotation_lab()
        panel = self.app.rotation_panel
        panel.worker = RotationWorker(self.temp.name)
        panel.worker.snapshots.put(dict(mode="SIMULATED", phase="UNKNOWN", detail="fixture status loss",
            estimated_deg=123, optical_truth_deg=128, intensity_au=1,
            valid_samples=0, rejected_samples=0, history=()))
        deadline = time.monotonic() + 2
        while panel.last_snapshot is None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIn("UNKNOWN", panel.estimate.cget("text"))
        self.assertIn("UNKNOWN", panel.truth.cget("text"))
        self.assertEqual(panel.intensity.cget("text"), "Intensity: UNKNOWN")

    def test_rotation_restart_ignores_previous_experiment_snapshot(self):
        self.app.rotation_lab()
        panel=self.app.rotation_panel
        panel.start()
        first=panel.experiment_id
        panel.cancel()
        deadline=time.monotonic()+5
        while panel.worker.busy.is_set() and time.monotonic()<deadline:
            self.root.update();time.sleep(.01)
        panel.start()
        second=panel.experiment_id
        self.assertNotEqual(first,second)
        # Inject a queued old terminal; it cannot enable Start or restore data.
        panel.worker.publish(dict(mode="SIMULATED",experiment_id=first,phase="COMPLETED",detail="old",
            estimated_deg=123,optical_truth_deg=128,intensity_au=1,valid_samples=5,rejected_samples=0,history=()))
        panel.root.after_cancel(panel.after_id)
        panel.after_id=None
        panel.poll()
        self.assertIsNone(panel.last_snapshot)
        self.assertEqual(str(panel.start_button.cget("state")),"disabled")
        self.assertIn("UNKNOWN",panel.estimate.cget("text"))
        panel.cancel()

    def test_rotation_worker_progresses_without_tk_polling(self):
        self.app.rotation_lab()
        panel = self.app.rotation_panel
        panel.start()
        time.sleep(.35)  # Deliberately do not update Tk; the model clock is worker-owned.
        value = panel.worker.snapshots.get(timeout=2)
        self.assertEqual(value["phase"], "SCANNING")
        self.assertTrue(value["output_active"])
        self.app.dismiss_overlay()
        deadline = time.monotonic() + 5
        while self.app.overlay is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(panel.worker.is_alive())
