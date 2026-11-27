"""SIMULATED finite rotating-QWP acquisition and bounded non-Tk worker."""
from collections import deque
from dataclasses import asdict
import queue
import threading

from polarimeter_core import JonesModel
from polarimeter_optical import OpticalAcquisition, OpticalClock, RotationLog
from polarimeter_rotation import MemoryRotationLink, RotationHost, simulated_demo


class RotationSession:
    def __init__(self, *, simulate, directory=None):
        if simulate is not True:
            raise ValueError("Rotation sessions require explicit SIMULATED mode")
        harness, config, plan = simulated_demo()
        self.link = MemoryRotationLink(harness, permit_simulated_reference=True)
        self.host = RotationHost(self.link, config, plan)
        clock = OpticalClock(10000, 50, 100)
        self.acquisition = OpticalAcquisition(plan=plan, exposure_us=10000, period_us=20000,
                                             detector_clock=clock, clock_mapping=clock)
        self.logger = None if directory is None else RotationLog(directory, metadata=dict(
            mode="SIMULATED", plant=asdict(harness.plant.parameters), pi=asdict(harness.pi.parameters),
            loop=asdict(harness.loop), timing=asdict(harness.controller.timing),
            readiness=asdict(harness.controller.readiness), config=config, plan=plan,
            detector_clock=asdict(clock), exposure_us=10000, period_us=20000,
            integration="trapezoidal interpolation of simulated optical truth"))
        self.history = deque(maxlen=1000)
        self._last_packet = self._last_sample = None
        self.now_us = 0
        self.closed = False
        self._speed_sum = self._true_speed_sum = self._phase_sum = self._intensity_sse = 0.0
        self._speed_min = self._true_speed_min = float("inf")
        self._speed_max = self._true_speed_max = float("-inf")
        self._first_angle = self._last_angle = None
        self.host.start(0)

    def step(self):
        if self.closed:
            raise RuntimeError("Simulation session is closed")
        received_us = self.now_us
        self.host.tick(received_us)
        self.now_us += 5000  # Keep device polling monotonic even after a disk/processing exception.
        p = self.host.latest
        if p is not None and p["seq"] != self._last_packet:
            self._last_packet = p["seq"]
            sample = self.acquisition.record(p)
            if self.logger is not None:
                self.logger.write({**p, "host_received_us": received_us}, sample)
            if sample is not None and sample["quality"] == "VALID":
                self._last_sample = sample
                self.history.append((sample["estimated_angle_deg"], sample["intensity_au"]))
                v, true_v = sample["estimated_velocity_deg_s"], sample["optical_truth_velocity_deg_s"]
                self._speed_sum += v
                self._true_speed_sum += true_v
                self._phase_sum += sample["estimated_angle_deg"] - sample["optical_truth_deg"]
                self._speed_min, self._speed_max = min(self._speed_min, v), max(self._speed_max, v)
                self._true_speed_min, self._true_speed_max = min(self._true_speed_min, true_v), max(self._true_speed_max, true_v)
                self._intensity_sse += (self.acquisition.model.intensity(sample["estimated_angle_deg"]) - sample["intensity_au"])**2
                if self._first_angle is None:
                    self._first_angle = sample["estimated_angle_deg"]
                self._last_angle = sample["estimated_angle_deg"]
        result = self.snapshot(include_history=False)
        return result

    def cancel(self):
        if not self.closed:
            self.host.cancel(self.now_us)

    def metrics(self):
        n = self.acquisition.valid
        return dict(mode="SIMULATED", reconstruction="PENDING", virtual_duration_s=self.now_us / 1e6,
            valid_samples=n, rejected_samples=self.acquisition.rejected,
            estimated_plateau_coverage_deg=None if n == 0 else self._last_angle - self._first_angle,
            mean_estimated_speed_deg_s=None if n == 0 else self._speed_sum / n,
            mean_true_optical_speed_deg_s=None if n == 0 else self._true_speed_sum / n,
            estimated_speed_ripple_deg_s=None if n == 0 else self._speed_max - self._speed_min,
            true_speed_ripple_deg_s=None if n == 0 else self._true_speed_max - self._true_speed_min,
            mean_estimated_minus_optical_angle_deg=None if n == 0 else self._phase_sum / n,
            intensity_rms_vs_inferred_angle_model=None if n == 0 else (self._intensity_sse / n)**.5)

    def snapshot(self, *, include_history=True):
        p = self.host.latest
        return dict(mode="SIMULATED", phase=self.host.phase, detail=self.host.detail,
            requested_end_deg=self.host.plan["end_deg"],
            estimated_deg=None if p is None else p["angle"],
            optical_truth_deg=None if p is None else p["truth"],
            velocity_deg_s=None if p is None else p["velocity"],
            intensity_au=None if p is None or self._last_sample is None else self._last_sample["intensity_au"],
            valid_samples=self.acquisition.valid, rejected_samples=self.acquisition.rejected,
            history=tuple(self.history) if include_history else (), output_active=None if p is None else p["active"],
            terminal=self.host.terminal,
            csv=None if self.logger is None else str(self.logger.sample_path))

    def finish(self):
        if self.closed:
            return
        try:
            if self.host.phase not in ("COMPLETED", "CANCELLED", "FAULT"):
                self.cancel()
                for _ in range(60):
                    self.host.tick(self.now_us)
                    self.now_us += 5000
                    if not self.link.harness.controller.output.active and self.host.phase != "CANCELLING":
                        break
            if self.logger is not None:
                self.logger.finish(phase=self.host.phase, detail=self.host.detail, metrics=self.metrics())
        finally:
            self.closed = True
            if self.logger is not None:
                self.logger.close()


class RotationWorker(threading.Thread):
    """Worker owns virtual time, model, protocol and disk; Tk consumes snapshots."""
    def __init__(self, directory):
        super().__init__(daemon=True, name="simulated-qwp-rotation")
        self.directory = directory
        self.commands = queue.Queue(maxsize=1)
        self.snapshots = queue.Queue(maxsize=1)
        self.stop_requested = threading.Event()
        self.shutdown_requested = threading.Event()
        self.busy = threading.Event()

    def start_scan(self):
        if self.busy.is_set() or self.shutdown_requested.is_set():
            return
        try:
            self.stop_requested.clear()
            self.busy.set()
            self.commands.put_nowait("START")
        except queue.Full:
            self.busy.clear()

    def publish(self, value):
        try:
            self.snapshots.get_nowait()
        except queue.Empty:
            pass
        self.snapshots.put_nowait(value)

    def run(self):
        session = None
        last_publish = 0
        try:
            while not self.shutdown_requested.is_set():
                try:
                    self.commands.get_nowait()
                    if session is None or session.closed:
                        session = RotationSession(simulate=True, directory=self.directory)
                except queue.Empty:
                    pass
                if session is not None and not session.closed:
                    if self.stop_requested.is_set():
                        session.cancel()
                        self.stop_requested.clear()
                    value = session.step()
                    if value["phase"] in ("COMPLETED", "CANCELLED", "FAULT", "UNKNOWN"):
                        session.finish()
                        value = session.snapshot()
                        self.busy.clear()
                    if session.closed or session.now_us - last_publish >= 100000:
                        self.publish(session.snapshot())
                        last_publish = session.now_us
                self.shutdown_requested.wait(.005)
        except Exception as error:
            if session is not None:
                session.host._unknown(session.now_us, "worker/acquisition error: " + str(error))
            self.publish(dict(mode="SIMULATED", phase="UNKNOWN", detail=str(error), history=(),
                              estimated_deg=None, optical_truth_deg=None, intensity_au=None,
                              valid_samples=0, rejected_samples=0, output_active=None))
        finally:
            self.busy.clear()
            if session is not None:
                try:
                    session.finish()
                except OSError:
                    pass


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    if not args.simulate:
        parser.error("Explicit --simulate is required; no hardware backend is available")
    session = RotationSession(simulate=True, directory=args.data_dir)
    try:
        for _ in range(10000):
            value = session.step()
            if value["phase"] in ("COMPLETED", "CANCELLED", "FAULT", "UNKNOWN"):
                break
        print(json.dumps(dict(phase=session.host.phase, **session.metrics()), indent=2))
        return 0 if session.host.phase == "COMPLETED" else 1
    finally:
        session.finish()


if __name__ == "__main__":
    raise SystemExit(main())
