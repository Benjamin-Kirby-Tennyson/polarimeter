"""SIMULATED rotation panel; Tk never owns the device clock or worker files."""
import queue
import tkinter as tk

from polarimeter_rotation_session import RotationWorker


class RotationPanel:
    def __init__(self, app, parent):
        self.app, self.root, self.parent = app, app.root, parent
        self.closed = self.closing = False
        self.after_id = self.close_id = None
        self.on_closed = None
        self._draw_count = 0
        self.worker = None
        self.last_snapshot = None
        self.experiment_id = None
        app.label("SIMULATED ROTATION LAB", 8, 6, 624, 30, size=22, parent=parent)
        app.label("3 turns • 30°/s fixture • physical control locked", 8, 40, 624, 26, size=16, parent=parent)
        app.label("Requested end: 1080° • Stokes reconstruction pending", 8, 70, 624, 24, size=16, parent=parent)
        self.estimate = app.label("Encoder estimate: UNKNOWN", 8, 100, 310, 24, size=17, parent=parent)
        self.truth = app.label("Optical truth: UNKNOWN", 322, 100, 310, 24, size=17, parent=parent)
        self.intensity = app.label("Intensity: — (uncalibrated a.u.)", 8, 128, 624, 24, size=16, parent=parent)
        self.plot = tk.Canvas(parent, bg="#1c3345", highlightthickness=0)
        self.plot.place(x=8, y=158, width=624, height=174)
        self.start_button = app.button("Start 3-turn scan", self.start, 8, 340, 200, 48, parent=parent)
        self.stop_button = app.button("Cancel scan", self.cancel, 216, 340, 200, 48, parent=parent)
        app.button("Back", app.dismiss_overlay, 432, 340, 200, 48, parent=parent)
        self.counts = app.label("0 valid optical samples", 8, 396, 624, 24, size=16, parent=parent)
        self.status = app.label("SIMULATED • ready • virtual time may run slower than wall time",
                                8, 424, 624, 50, size=15, parent=parent)
        self.status.configure(wraplength=620, anchor="nw", justify="left")
        self.stop_button.configure(state="disabled")
        self.draw(())
        self.after_id = self.root.after(100, self.poll)

    def start(self):
        if self.closing:
            return
        if self.worker is None or not self.worker.is_alive():
            self.worker = RotationWorker(self.app.controller.directory)
            self.worker.start()
        self.experiment_id = self.worker.start_scan()
        self.last_snapshot = None
        self.estimate.configure(text="Encoder estimate: UNKNOWN")
        self.truth.configure(text="Optical truth: UNKNOWN")
        self.intensity.configure(text="Intensity: — (uncalibrated a.u.)")
        self.draw(())
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.configure(text="SIMULATED • connecting and preparing one finite scan")

    def cancel(self):
        if self.worker is not None:
            self.worker.stop_requested.set()
        self.status.configure(text="SIMULATED • stop requested; mechanical stopping not confirmed")

    def poll(self):
        self.after_id = None
        if self.closed or self.closing:
            return
        if self.worker is not None:
            try:
                value = self.worker.snapshots.get_nowait()
            except queue.Empty:
                value = None
            if value is not None and self.experiment_id is not None and value.get("experiment_id") != self.experiment_id:
                value = None
            if value is not None:
                self.last_snapshot = value
                phase = value["phase"]
                unavailable = phase == "UNKNOWN"
                angle, truth = value.get("estimated_deg"), value.get("optical_truth_deg")
                self.estimate.configure(text="Encoder estimate: UNKNOWN" if unavailable or angle is None else f"Encoder estimate: {angle:.2f}°")
                self.truth.configure(text="Optical truth: UNKNOWN" if unavailable or truth is None else f"Optical truth (sim): {truth:.2f}°")
                intensity = value.get("intensity_au")
                self.intensity.configure(text="Intensity: UNKNOWN" if unavailable else "Intensity: — (uncalibrated a.u.)" if intensity is None else f"Intensity: {intensity:.4f} uncalibrated a.u.")
                self.counts.configure(text=f"{value['valid_samples']} valid / {value['rejected_samples']} flagged exposures")
                done = phase in ("COMPLETED", "CANCELLED", "FAULT", "UNKNOWN")
                self.start_button.configure(state="normal" if done and not self.worker.busy.is_set() else "disabled")
                self.stop_button.configure(state="disabled" if done else "normal")
                self.status.configure(text=f"SIMULATED • {phase} • {value['detail']}")
                self._draw_count += 1
                if self._draw_count % 2 == 0 or done:
                    self.draw(value.get("history", ()))
        self.after_id = self.root.after(100, self.poll)

    def draw(self, points):
        c = self.plot
        c.delete("all")
        c.create_text(312, 12, text="SIMULATED intensity vs encoder-inferred QWP angle", fill="#f1f6fa")
        c.create_text(312, 160, text="Uncalibrated a.u. • bounded display history", fill="#b6cbd8")
        if len(points) < 2:
            return
        low, high = points[0][0], points[-1][0]
        if high <= low:
            return
        coords = []
        for angle, intensity in points[-1000:]:
            coords.extend((30 + (angle - low) * 570 / (high - low), 140 - intensity * 110))
        c.create_line(*coords, fill="#58ddc2", width=2)

    def request_close(self, callback):
        self.on_closed = callback
        if self.closed:
            callback()
            return
        if self.closing:
            return
        self.closing = True
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.worker is not None:
            self.worker.stop_requested.set()
            self.worker.shutdown_requested.set()
        self._wait_closed()

    def _wait_closed(self):
        if self.worker is not None and self.worker.is_alive():
            self.close_id = self.root.after(20, self._wait_closed)
            return
        self.closed = True
        self.close_id = None
        if self.on_closed is not None:
            self.on_closed()
