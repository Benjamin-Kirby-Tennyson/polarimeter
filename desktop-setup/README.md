# Brave and Pi Health desktop setup

Run this in a normal terminal on the Pi as `cuqit` (the script uses `sudo` only
for Debian package installation):

```sh
bash /home/cuqit/polarimeter/desktop-setup/setup-desktop.sh
```

The script installs the monitor first, creates **Pi Health** and **Brave**
desktop shortcuts, and starts the monitor at desktop login. Brave is installed
from its official signed APT repository and receives updates through APT. It
does not start Brave automatically or change the default browser.

Pi Health uses the existing system Python/GTK packages, reads local system
counters every three seconds, and keeps no logs or growing sample history.
Opening its desktop shortcut again brings the existing window forward.
Closing the window stops monitoring until it is opened again or the next login.
The optional keep-above setting depends on the window manager; Wayland may
ignore it. Desktop notifications also depend on the desktop notification service.

Alerts:

- Temperature: yellow at 70°C, red at 80°C. These are monitor thresholds;
  the Pi firmware manages throttling independently.
- CPU: yellow after at least 30 seconds at 90% or higher across all cores.
  High CPU usage alone is a workload indicator, not hardware damage.
- RAM: yellow at 90%, red at 95%, calculated using Linux `MemAvailable`
  so reclaimable cache is not treated as exhausted memory.
- Root filesystem: yellow at 90%, red at 95% used.
- Firmware: current undervoltage/capping/throttling flags are red. Historical
  flags since boot are displayed separately. Unavailable firmware access is
  labelled explicitly and is never treated as proof of no throttling.

Temperature prefers the firmware reading, falling back to the labelled Linux
thermal sensor. Firmware throttling flags refresh every 15 seconds. Swap used,
one-minute system load and free storage are also displayed. This monitor does
not control fans, CPU speed, GPIO, serial devices or motors.

Read a real sample without opening a window:

```sh
/usr/bin/python3 /home/cuqit/polarimeter/desktop-setup/pi_health.py --once
```

If the desktop asks whether to execute a launcher, choose **Execute** / **Allow
Launching**. To disable automatic startup, remove
`~/.config/autostart/pi-health-monitor.desktop`.

During preparation, this agent session could read the Linux temperature and
CPU/RAM/storage counters, but could not reach the graphical display or firmware
device, resolve Brave's download host, use sudo, or write outside the project
and `/tmp`. Consequently, installation and GUI/notification behaviour have not
been verified here. Running the setup command in a normal desktop terminal is
still required. The script reports failures and can be rerun; monitor setup
remains installed if Brave's download or package installation fails.

Sources:

- https://brave.com/linux/ — official ARM64 and signed APT setup.
- https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#frequency-management-and-thermal-control
  — thermal behaviour and temperature measurement.
- https://www.raspberrypi.com/documentation/computers/os.html#get_throttled
  — current and historical firmware flag definitions.
