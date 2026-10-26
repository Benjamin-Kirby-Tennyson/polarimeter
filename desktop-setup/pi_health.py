#!/usr/bin/python3
"""Small Pi desktop health display. No pip packages, logs or sample history."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


FLAGS = ("Low supply voltage", "CPU frequency capped", "Throttling", "Temperature limit")


def cpu_counters(text):
    # Guest time is already counted in user/nice; do not count it twice.
    values = [int(v) for v in text.splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def cpu_percent(previous, current):
    total = current[0] - previous[0]
    idle = current[1] - previous[1]
    return max(0, min(100, 100 * (total - idle) / total)) if total > 0 else None


def throttle_flags(value, historical=False):
    if value is None:
        return None
    offset = 16 if historical else 0
    return [name for bit, name in enumerate(FLAGS) if value & (1 << (bit + offset))]


def assess(sample, busy_seconds):
    """Return severity and actionable reasons; old firmware flags aren't current faults."""
    problems = []
    temp = sample["temperature_c"]
    if temp is not None and temp >= 80:
        problems.append((2, "HOT: improve cooling / reduce workload"))
    elif temp is not None and temp >= 70:
        problems.append((1, "Warm: check airflow"))
    if sample["cpu_percent"] is not None and sample["cpu_percent"] >= 90 and busy_seconds >= 30:
        problems.append((1, "CPU busy for 30+ seconds: close unused apps"))
    ram = sample["ram_percent"]
    if ram >= 90:
        problems.append((2 if ram >= 95 else 1, "RAM nearly full: close tabs / apps"))
    disk = sample["disk_percent"]
    if disk >= 90:
        problems.append((2 if disk >= 95 else 1, "Storage nearly full: free disk space"))
    active = throttle_flags(sample["throttled"])
    if active:
        problems.append((2, "Current: " + ", ".join(active)))
    if problems:
        return max(level for level, _ in problems), [message for _, message in problems]
    if temp is None or active is None:
        return 0, ["Readings OK; some sensors unavailable"]
    return 0, ["Readings OK"]


class Sampler:
    def __init__(self):
        self.previous_cpu = None
        self.throttled = None
        self.last_firmware = float("-inf")
        self.vcgencmd = shutil.which("vcgencmd")

    def firmware_read(self, command):
        if not self.vcgencmd:
            return None
        try:
            result = subprocess.run(
                [self.vcgencmd, command], capture_output=True, text=True,
                timeout=0.5, check=True,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def sample(self):
        now = time.monotonic()
        if now - self.last_firmware >= 15:
            self.last_firmware = now
            raw = self.firmware_read("get_throttled")
            match = re.fullmatch(r"throttled=(0x[0-9a-fA-F]+)", raw or "")
            self.throttled = int(match[1], 16) if match else None
        # Prefer the firmware sensor; show the sysfs source explicitly on failure.
        raw_temp = self.firmware_read("measure_temp")
        match = re.fullmatch(r"temp=([0-9.]+)'C", raw_temp or "")
        temperature = float(match[1]) if match else None
        temperature_source = "firmware"
        if temperature is None:
            temperature_source = "Linux sensor"
            try:
                temperature = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
            except (OSError, ValueError):
                temperature_source = "unavailable"
        current = cpu_counters(Path("/proc/stat").read_text())
        cpu = cpu_percent(self.previous_cpu, current) if self.previous_cpu else None
        self.previous_cpu = current
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, value = line.split(":", 1)
            memory[name] = int(value.split()[0])
        total = memory["MemTotal"]
        available = memory["MemAvailable"]
        swap_used = memory["SwapTotal"] - memory["SwapFree"]
        disk = shutil.disk_usage("/")
        return {
            "temperature_c": temperature, "temperature_source": temperature_source,
            "cpu_percent": cpu, "ram_percent": 100 * (total - available) / total,
            "ram_available_mib": available / 1024, "ram_total_mib": total / 1024,
            "swap_used_mib": swap_used / 1024,
            "swap_total_mib": memory["SwapTotal"] / 1024,
            "disk_percent": 100 * disk.used / disk.total,
            "disk_free_gib": disk.free / 1024**3,
            "load_1m": os.getloadavg()[0], "cpu_cores": os.cpu_count(),
            "throttled": self.throttled,
        }


def run_gui():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, GLib, Gtk

    class Monitor(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="local.polarimeter.PiHealth")
            self.window = None
            self.sampler = Sampler()
            self.busy_since = None
            self.last_notification = float("-inf")
            self.last_alert = None

        def do_activate(self):
            if self.window:
                self.window.present()
                return
            self.window = Gtk.ApplicationWindow(application=self, title="Raspberry Pi Health")
            self.window.set_icon_name("utilities-system-monitor")
            self.window.set_default_size(365, 430)
            self.window.set_border_width(16)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.window.add(box)
            title = Gtk.Label()
            title.set_markup("<b>Raspberry Pi Health</b>")
            box.pack_start(title, False, False, 0)
            self.status = Gtk.Label(xalign=0)
            self.status.set_line_wrap(True)
            box.pack_start(self.status, False, False, 0)
            self.metrics = {}
            for key in ("Temperature", "CPU", "RAM", "Storage"):
                label = Gtk.Label(xalign=0)
                bar = Gtk.ProgressBar()
                box.pack_start(label, False, False, 0)
                box.pack_start(bar, False, False, 0)
                self.metrics[key] = label, bar
            self.details = Gtk.Label(xalign=0)
            self.details.set_line_wrap(True)
            self.details.set_selectable(True)
            box.pack_start(self.details, False, False, 0)
            note = Gtk.Label(label="Updates every 3s • Yellow: warm / busy • Red: act now")
            note.set_line_wrap(True)
            box.pack_start(note, False, False, 0)
            pin = Gtk.CheckButton(label="Keep above other windows (if desktop supports it)")
            pin.connect("toggled", lambda button: self.window.set_keep_above(button.get_active()))
            box.pack_start(pin, False, False, 0)
            self.window.show_all()
            self.refresh()
            GLib.timeout_add_seconds(3, self.refresh)

        def metric(self, name, text, percent):
            label, bar = self.metrics[name]
            label.set_text(text)
            bar.set_fraction(max(0, min(1, percent / 100)))

        def refresh(self):
            try:
                sample = self.sampler.sample()
            except (OSError, ValueError, KeyError) as error:
                self.status.set_text("Cannot read system health: " + str(error))
                return True
            now = time.monotonic()
            cpu = sample["cpu_percent"]
            if cpu is not None and cpu >= 90:
                if self.busy_since is None:
                    self.busy_since = now
            else:
                self.busy_since = None
            busy = now - self.busy_since if self.busy_since is not None else 0
            level, reasons = assess(sample, busy)
            color = ("#237b35", "#9a6500", "#be2424")[level]
            heading = ("MONITORING", "WARNING", "CHECK PI NOW")[level]
            self.status.set_markup(
                '<span foreground="' + color + '"><b>' + heading + "</b></span>\n"
                + GLib.markup_escape_text("\n".join(reasons))
            )
            temp = sample["temperature_c"]
            temp_text = f"{temp:.1f} °C" if temp is not None else "unavailable"
            self.metric("Temperature", f"Temperature: {temp_text} ({sample['temperature_source']})", temp or 0)
            self.metric("CPU", f"CPU: {cpu:.0f}% across all cores" if cpu is not None else "CPU: measuring…", cpu or 0)
            self.metric("RAM", f"RAM: {sample['ram_percent']:.0f}% used • {sample['ram_available_mib']:.0f} MiB available", sample["ram_percent"])
            self.metric("Storage", f"Storage: {sample['disk_percent']:.0f}% used • {sample['disk_free_gib']:.1f} GiB free", sample["disk_percent"])
            current = throttle_flags(sample["throttled"])
            history = throttle_flags(sample["throttled"], historical=True)
            current_text = ", ".join(current) if current else "None" if current == [] else "Unavailable"
            history_text = ", ".join(history) if history else "None" if history == [] else "Unavailable"
            self.details.set_text(
                f"Swap: {sample['swap_used_mib']:.0f} / {sample['swap_total_mib']:.0f} MiB\n"
                f"Load (1 min): {sample['load_1m']:.2f} / {sample['cpu_cores']} cores\n"
                f"Firmware flags (every 15s): {current_text}\n"
                f"Earlier since boot: {history_text}\n"
                "Temperature: warning 70°C • hot 80°C\n"
                "CPU warning: ≥90% for 30s • RAM: ≥90%"
            )
            alert = tuple(reasons) if level else None
            if alert and (alert != self.last_alert or now - self.last_notification >= 300):
                notification = Gio.Notification.new("Raspberry Pi health warning")
                notification.set_body("\n".join(reasons))
                notification.set_icon(Gio.ThemedIcon.new("dialog-warning"))
                if level == 2:
                    notification.set_priority(Gio.NotificationPriority.URGENT)
                self.send_notification("pi-health", notification)
                self.last_notification = now
            self.last_alert = alert
            return True

    return Monitor().run([])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Print one real sample as JSON, without a GUI")
    args = parser.parse_args()
    if args.once:
        sampler = Sampler()
        sampler.sample()
        time.sleep(1)
        sample = sampler.sample()
        level, reasons = assess(sample, 0)
        print(json.dumps(dict(sample, severity=level, status=reasons), indent=2))
        return 0
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
