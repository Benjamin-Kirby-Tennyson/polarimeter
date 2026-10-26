import unittest

from pi_health import assess, cpu_counters, cpu_percent, throttle_flags


class HealthTests(unittest.TestCase):
    def sample(self, **changes):
        result = dict(temperature_c=45, cpu_percent=25, ram_percent=60,
                      disk_percent=30, throttled=0)
        result.update(changes)
        return result

    def test_cpu_excludes_guest_and_counts_iowait_as_idle(self):
        self.assertEqual(cpu_counters("cpu 100 10 20 500 30 2 3 4 40 5\ncpu0 0"), (669, 530))
        self.assertAlmostEqual(cpu_percent((100, 40), (200, 100)), 40)
        self.assertIsNone(cpu_percent((100, 40), (100, 40)))

    def test_temperature_thresholds(self):
        self.assertEqual(assess(self.sample(temperature_c=69.9), 0)[0], 0)
        self.assertEqual(assess(self.sample(temperature_c=70), 0)[0], 1)
        self.assertEqual(assess(self.sample(temperature_c=80), 0)[0], 2)

    def test_busy_cpu_requires_sustained_load(self):
        sample = self.sample(cpu_percent=95)
        self.assertEqual(assess(sample, 29)[0], 0)
        self.assertEqual(assess(sample, 30)[0], 1)
        self.assertEqual(assess(self.sample(cpu_percent=20), 60)[0], 0)

    def test_memory_and_disk_thresholds(self):
        for key in ("ram_percent", "disk_percent"):
            self.assertEqual(assess(self.sample(**{key: 89.9}), 0)[0], 0)
            self.assertEqual(assess(self.sample(**{key: 90}), 0)[0], 1)
            self.assertEqual(assess(self.sample(**{key: 95}), 0)[0], 2)

    def test_historical_flags_are_not_current_failures(self):
        sample = self.sample(throttled=0x50000)
        self.assertEqual(throttle_flags(sample["throttled"]), [])
        self.assertEqual(throttle_flags(sample["throttled"], True), ["Low supply voltage", "Throttling"])
        self.assertEqual(assess(sample, 0)[0], 0)
        self.assertEqual(assess(self.sample(throttled=0x50005), 0)[0], 2)

    def test_missing_sensors_remain_explicit(self):
        self.assertIsNone(throttle_flags(None))
        level, messages = assess(self.sample(temperature_c=None, throttled=None), 0)
        self.assertEqual(level, 0)
        self.assertIn("unavailable", " ".join(messages))


if __name__ == "__main__":
    unittest.main()
