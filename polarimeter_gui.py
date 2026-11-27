"""640x480 Tk UI; optional USB metadata discovery, no hardware transport."""
import math
import sys
import tkinter as tk

from polarimeter_core import SIMULATED, ScanController, target_angle


BG = "#10202e"
PANEL = "#1c3345"
FG = "#f1f6fa"
MUTED = "#b6cbd8"
ACCENT = "#58ddc2"


class PolarimeterApp:
    WIDTH, HEIGHT = 640, 480

    def __init__(self, root, controller, *, rate=20, fullscreen=True):
        if not math.isfinite(rate) or not 1 <= rate <= 50:
            raise ValueError("GUI sample rate must be between 1 and 50 Hz")
        self.root, self.controller = root, controller
        self.interval_ms = max(20, round(1000 / rate))
        self.tick_id = self.draw_id = None
        self.overlay = None
        self.rotation_panel = None
        self.closed = False
        self.last_error = None
        root.title("Rotating QWP polarimeter")
        root.configure(bg=BG)
        root.geometry("640x480")
        root.resizable(False, False)
        if fullscreen:
            root.attributes("-fullscreen", True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda event: self.escape())
        root.report_callback_exception = self.callback_error
        self.body = tk.Frame(root, bg=BG, width=640, height=480)
        self.body.place(relx=.5, rely=.5, anchor="center", width=640, height=480)
        self.label("ROTATING QWP POLARIMETER", 8, 4, 510, 28, size=19)
        self.button("Exit", self.close, 540, 4, 92, 44)
        mode = ("SIMULATED • physical control disabled" if self.simulated
                else "HARDWARE LOCKED • protocol / safety unverified")
        self.label(mode, 8, 36, 524, 24, size=16, color=ACCENT if self.simulated else "#ffce75")
        self.angle = self.label("QWP: —", 8, 66, 295, 34, size=24)
        self.intensity = self.label("Intensity: —", 310, 66, 322, 34, size=24)
        self.plot = tk.Canvas(self.body, bg=PANEL, highlightthickness=0)
        self.plot.place(x=8, y=104, width=624, height=190)
        self.start_button = self.button("Start Scan", self.start, 8, 300, 200, 48)
        self.stop_button = self.button("Stop", self.stop, 216, 300, 128, 48, bg="#8e3644")
        self.button("Motor / USB", self.motor_status, 352, 300, 136, 48)
        self.rotation_button = self.button("Rotation lab", self.rotation_lab, 496, 300, 136, 48)
        self.ccw_button = self.button("CCW −5°", lambda: self.move(jog=-5), 8, 356, 112, 48)
        self.cw_button = self.button("CW +5°", lambda: self.move(jog=5), 128, 356, 112, 48)
        self.target = tk.StringVar(value="90")
        self.target_button = self.button("Target 90°", self.keypad, 248, 356, 240, 48)
        self.go_button = self.button("Go", self.move, 496, 356, 136, 48)
        self.label("Model angle • uncalibrated a.u." if self.simulated else
                   "No angle feedback or detector acquisition configured", 8, 408, 624, 20,
                   size=14, color=MUTED)
        self.status = self.label("Ready • CSV: data/simulated/" if self.simulated else
                                 "Launch with --gui --simulate for simulated scans", 8, 430,
                                 624, 44, size=14, color=MUTED)
        self.status.configure(wraplength=620, justify="left", anchor="nw")
        self.manual_buttons = (self.ccw_button, self.cw_button,
                               self.target_button, self.go_button)
        self.set_controls()
        self.draw()

    @property
    def simulated(self):
        return self.controller.mode == SIMULATED

    def label(self, text, x, y, width, height, *, size=16, color=FG, parent=None):
        widget = tk.Label(parent or self.body, text=text, bg=BG, fg=color,
                          font=("DejaVu Sans", -size), anchor="w")
        widget.place(x=x, y=y, width=width, height=height)
        return widget

    def button(self, text, command, x, y, width, height, *, parent=None, bg=PANEL):
        widget = tk.Button(parent or self.body, text=text, command=command, bg=bg, fg=FG,
                           activebackground="#30536a", activeforeground=FG,
                           disabledforeground="#82939d", font=("DejaVu Sans", -19),
                           relief="flat", borderwidth=0, highlightthickness=0)
        widget.place(x=x, y=y, width=width, height=height)
        return widget

    def set_controls(self):
        self.start_button.configure(state="normal" if self.simulated and not self.controller.running else "disabled")
        for button in self.manual_buttons:
            button.configure(state="normal" if self.simulated and not self.controller.running else "disabled")
        self.stop_button.configure(state="normal" if self.simulated else "disabled")
        self.rotation_button.configure(state="normal" if self.simulated and not self.controller.running else "disabled")

    def cancel_tick(self):
        if self.tick_id is not None:
            self.root.after_cancel(self.tick_id)
            self.tick_id = None

    def fail(self, error):
        self.last_error = str(error)
        self.cancel_tick()
        try:
            self.controller.stop()
        except OSError as close_error:
            self.last_error += f"; closing CSV: {close_error}"
        self.status.configure(text=f"ERROR • {self.last_error}", fg="#ffce75")
        print(f"{self.controller.mode}: {self.last_error}", file=sys.stderr)
        self.set_controls()

    def callback_error(self, exc_type, value, traceback):
        self.fail(value)

    def start(self):
        try:
            self.cancel_tick()
            self.controller.start()
            self.status.configure(text="SIMULATED scan: 0–360°, 2° steps • CSV recording", fg=MUTED)
            print(f"SIMULATED: logging to {self.controller.last_path}", flush=True)
            self.set_controls()
            self.tick()
        except (OSError, ValueError, RuntimeError) as error:
            self.fail(error)

    def tick(self):
        self.tick_id = None
        try:
            point = self.controller.tick()
            if point:
                self.show_point(point)
            if self.controller.running:
                self.tick_id = self.root.after(self.interval_ms, self.tick)
            else:
                self.finished("Scan complete")
        except (OSError, ValueError, RuntimeError) as error:
            self.fail(error)

    def show_point(self, point):
        angle, intensity = point
        self.angle.configure(text=f"QWP: {angle:.1f}°")
        self.intensity.configure(text=f"I: {intensity:.4f} a.u.")
        # At most five plot redraws/second, including manual moves.
        if self.draw_id is None:
            self.draw_id = self.root.after(200, self.draw)

    def draw(self):
        self.draw_id = None
        c = self.plot
        c.delete("all")
        c.create_text(312, 13, text=f"{self.controller.mode} • intensity vs QWP angle",
                      fill=FG, font=("DejaVu Sans", -14))
        left, right, top, bottom = 48, 604, 34, 146
        for angle in (0, 90, 180, 270, 360):
            x = left + (right - left) * angle / 360
            c.create_line(x, top, x, bottom, fill="#365064")
            c.create_text(x, bottom + 13, text=str(angle), fill=MUTED, font=("DejaVu Sans", -12))
        maximum = max([1.0] + [p[1] for p in self.controller.history])
        for fraction in (0, .5, 1):
            y = bottom - (bottom - top) * fraction
            c.create_line(left, y, right, y, fill="#365064")
            c.create_text(left - 6, y, text=f"{maximum * fraction:.2g}", anchor="e",
                          fill=MUTED, font=("DejaVu Sans", -12))
        c.create_text(320, 179, text="Model QWP angle (°) • intensity (uncalibrated a.u.)" if self.simulated
                      else "No instrument data", fill=MUTED, font=("DejaVu Sans", -12))
        points = []
        for angle, value in self.controller.history:
            points.extend((left + (right - left) * angle / 360,
                           bottom - (bottom - top) * value / maximum))
        if len(points) >= 4:
            c.create_line(*points, fill=ACCENT, width=2)
        if points:
            x, y = points[-2:]
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ACCENT, outline="")

    def finished(self, reason):
        path = self.controller.last_path
        text = f"{reason} • CSV: {path.name}" if path else reason
        self.status.configure(text=text, fg=MUTED)
        self.set_controls()

    def stop(self):
        self.cancel_tick()
        try:
            self.controller.stop()
            self.finished("Stopped locally")
        except (OSError, RuntimeError) as error:
            self.fail(error)

    def move(self, *, jog=None):
        try:
            point = self.controller.jog(jog) if jog is not None else self.controller.move_to(self.target.get())
            self.show_point(point)
            self.status.configure(text=f"SIMULATED manual reading • CSV: {self.controller.last_path.name}", fg=MUTED)
        except (OSError, ValueError, RuntimeError) as error:
            self.fail(error)

    def dismiss_overlay(self):
        if self.rotation_panel is not None and not self.rotation_panel.closed:
            self.rotation_panel.request_close(self.dismiss_overlay)
            return
        self.rotation_panel = None
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None

    def rotation_lab(self):
        if not self.simulated or self.controller.running or self.rotation_panel is not None:
            return
        self.stop()
        self.dismiss_overlay()
        from polarimeter_rotation_gui import RotationPanel
        panel = self.overlay = tk.Frame(self.body, bg=BG)
        panel.place(x=0, y=0, width=640, height=480)
        self.rotation_panel = RotationPanel(self, panel)

    def motor_status(self):
        """Show USB metadata on request; never connect, read or command a motor."""
        self.dismiss_overlay()
        panel = self.overlay = tk.Frame(self.body, bg=BG)
        panel.place(x=0, y=0, width=640, height=480)
        self.label("DC MOTOR / USB", 8, 8, 624, 30, size=22, parent=panel)
        self.label("Physical control locked • no measured motor position",
                   8, 44, 624, 28, size=17, color="#ffce75", parent=panel)
        self.label("Rotation buttons change the simulation only." if self.simulated
                   else "Scan, target and jog controls remain disabled.",
                   8, 76, 624, 26, size=16, parent=panel)
        self.label("USB devices below are not verified motor controllers.",
                   8, 106, 624, 26, size=16, parent=panel)
        details = tk.Text(panel, bg=PANEL, fg=FG, wrap="word", relief="flat",
                          font=("DejaVu Sans", -16), padx=8, pady=8,
                          state="disabled", takefocus=False)
        details.place(x=8, y=140, width=600, height=210)
        scrollbar = tk.Scrollbar(panel, command=details.yview)
        scrollbar.place(x=608, y=140, width=24, height=210)
        details.configure(yscrollcommand=scrollbar.set)
        self.label("Discovery does not open ports or send commands.",
                   8, 356, 624, 24, size=16, color=MUTED, parent=panel)
        self.label("TB6612FNG driver reported • wiring / protocol unverified.",
                   8, 382, 624, 24, size=16, color=MUTED, parent=panel)

        def refresh():
            try:
                from polarimeter_serial import discover_usb_devices
                devices = discover_usb_devices()
                lines = []
                for device in devices:
                    lines.extend((f"USB serial bridge: {device.device}",
                                  f"USB ID: {device.vid:04x}:{device.pid:04x}",
                                  f"Serial: {device.serial_number or 'not reported'}",
                                  f"Location: {device.location or 'not reported'}"))
                    if device.by_id:
                        lines.append("By ID: " + ", ".join(device.by_id))
                    if device.by_path:
                        lines.append("By path: " + ", ".join(device.by_path))
                    lines.append("")
                text = "\n".join(lines) if devices else "No USB serial devices visible in this session."
            except (ImportError, OSError, RuntimeError) as error:
                text = f"USB discovery unavailable: {error}"
            details.configure(state="normal")
            details.delete("1.0", "end")
            details.insert("1.0", text)
            details.configure(state="disabled")

        self.button("Refresh USB", refresh, 8, 420, 200, 48, parent=panel)
        self.button("Check touch", self.touch_check, 216, 420, 200, 48, parent=panel)
        self.button("Back", self.dismiss_overlay, 432, 420, 200, 48, parent=panel)
        refresh()

    def keypad(self):
        if not self.simulated or self.controller.running:
            return
        self.dismiss_overlay()
        panel = self.overlay = tk.Frame(self.body, bg=BG)
        panel.place(x=116, y=40, width=408, height=428)
        draft = tk.StringVar(value=self.target.get())
        self.label("SIMULATED target • 0–360°", 8, 4, 392, 28, parent=panel)
        display = self.label("", 8, 36, 392, 38, size=25, parent=panel)
        display.configure(textvariable=draft, bg=PANEL)
        message = self.label("Tap Clear to enter a new angle", 8, 78, 392, 28, size=14, parent=panel)

        def key(value):
            if value == "Clear":
                draft.set("")
            elif len(draft.get()) < 9 and (value != "." or "." not in draft.get()):
                draft.set(draft.get() + value)

        for index, value in enumerate(("7", "8", "9", "4", "5", "6", "1", "2", "3", "Clear", "0", ".")):
            self.button(value, lambda v=value: key(v), 8 + index % 3 * 132,
                        110 + index // 3 * 56, 128, 50, parent=panel)

        def accept():
            try:
                angle = target_angle(draft.get())
            except ValueError:
                message.configure(text="Enter a finite angle from 0 to 360°", fg="#ffce75")
                return
            self.target.set(f"{angle:g}")
            self.target_button.configure(text=f"Target {angle:g}°")
            self.dismiss_overlay()

        self.button("⌫", lambda: draft.set(draft.get()[:-1]), 8, 340, 128, 50, parent=panel)
        self.button("Cancel", self.dismiss_overlay, 140, 340, 128, 50, parent=panel)
        self.button("Set", accept, 272, 340, 128, 50, parent=panel)

    def touch_check(self):
        self.stop()
        self.dismiss_overlay()
        panel = self.overlay = tk.Frame(self.body, bg=BG)
        panel.place(x=0, y=0, width=640, height=480)
        self.label("INPUT CHECK • tap targets 1–5 with your finger", 74, 66, 520, 36,
                   size=18, parent=panel)
        self.label("Mouse clicks also register; this cannot identify touch hardware.",
                   40, 106, 590, 30, size=15, parent=panel)
        progress = self.label("0 / 5 pointer targets received", 100, 280, 520, 36,
                              size=18, parent=panel)
        positions = ((8, 8), (568, 8), (288, 184), (8, 408), (568, 408))
        self.touch_index = 0

        def received():
            self.touch_index += 1
            if self.touch_index == 5:
                target.destroy()
                progress.configure(text="5 / 5 • confirm you used finger taps", fg=ACCENT)
                self.status.configure(text="Input check: 5/5 pointer targets received; touch source unverified")
            else:
                x, y = positions[self.touch_index]
                target.place_configure(x=x, y=y)
                target.configure(text=str(self.touch_index + 1))
                progress.configure(text=f"{self.touch_index} / 5 pointer targets received")

        target = self.button("1", received, *positions[0], 64, 64, parent=panel)
        self.button("Back", self.dismiss_overlay, 240, 360, 160, 48, parent=panel)

    def escape(self):
        if self.overlay is not None:
            self.dismiss_overlay()
        else:
            self.close()

    def close(self):
        if self.closed:
            return
        if self.rotation_panel is not None and not self.rotation_panel.closed:
            self.rotation_panel.request_close(self.close)
            return
        self.cancel_tick()
        if self.draw_id is not None:
            self.root.after_cancel(self.draw_id)
            self.draw_id = None
        try:
            self.controller.stop()
        except OSError as error:
            print(f"Error closing CSV: {error}", file=sys.stderr)
        finally:
            self.closed = True
            self.root.destroy()


def run_gui(*, simulate=False, directory="data", window=361, rate=20,
            fullscreen=True, touch_check=False):
    controller = ScanController(simulate=simulate, directory=directory, window=window)
    root = None
    try:
        root = tk.Tk()
        if root.winfo_screenwidth() < 640 or root.winfo_screenheight() < 480:
            raise ValueError("This interface needs at least 640×480; check display settings")
        app = PolarimeterApp(root, controller, rate=rate, fullscreen=fullscreen)
        print(f"{controller.mode}: Tk screen {root.winfo_screenwidth()}×{root.winfo_screenheight()}; physical control disabled", flush=True)
        if touch_check:
            app.touch_check()
        root.mainloop()
        return 0
    except tk.TclError as error:
        print(f"Tk display unavailable: {error}. Run from the Pi desktop; no configuration was changed.", file=sys.stderr)
        return 1
    finally:
        controller.stop()
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
