import csv
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from polarimeter import parse_sample


PROJECT = Path(__file__).resolve().parents[1]


class LegacyTests(unittest.TestCase):
    def test_existing_parsers(self):
        self.assertEqual(parse_sample(" 1.5 ", "float", 0, "value"), 1.5)
        self.assertEqual(parse_sample("0,2.5", "csv", 1, "value"), 2.5)
        self.assertEqual(parse_sample('{"detector": 3.5}', "json", 0, "detector"), 3.5)
        for text in ("nan", "inf", "-inf", "abc"):
            with self.assertRaises(ValueError):
                parse_sample(text, "float", 0, "value")

    def test_original_simulation_cli_and_exclusive_csv_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.csv"
            command = [sys.executable, str(PROJECT / "polarimeter.py"), "--simulate",
                       "--no-plot", "--seconds", ".05", "--rate", "100", "--output", str(path)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertGreater(len(rows), 0)
            self.assertEqual({r["mode"] for r in rows}, {"SIMULATED"})
            original = path.read_bytes()
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), original)

    def test_gui_rejects_serial_options_before_import_or_open(self):
        for args in (("--port", "/dev/DO_NOT_OPEN"), ("--baud", "115200"),
                     ("--rate", "nan"), ("--window", "100000"), ("--output", "unused.csv")):
            result = subprocess.run([sys.executable, str(PROJECT / "polarimeter.py"),
                                     "--gui", *args], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_gui_import_does_not_load_hardware_or_heavy_packages(self):
        code = ("import sys; import polarimeter_gui; "
                "assert not {'serial', 'numpy', 'matplotlib', 'scipy', 'pyvisa'} & set(sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gui_missing_display_reports_actionable_error_without_acquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, str(PROJECT / "polarimeter.py"), "--gui", "--simulate",
                 "--data-dir", folder], capture_output=True, text=True, timeout=10,
                env={**os.environ, "DISPLAY": "", "WAYLAND_DISPLAY": ""})
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Run from the Pi desktop", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])


def load_tests(loader, tests, pattern):
    # Include the existing desktop suite in root unittest discovery without
    # renaming the working desktop-setup directory or modifying its code.
    spec = importlib.util.spec_from_file_location("pi_health", PROJECT / "desktop-setup/pi_health.py")
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    sys.modules["pi_health"] = health
    spec = importlib.util.spec_from_file_location("test_pi_health", PROJECT / "desktop-setup/test_pi_health.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tests.addTests(loader.loadTestsFromModule(module))
    return tests
