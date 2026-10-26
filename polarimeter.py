#!/usr/bin/env python3
"""Read-only detector acquisition; simulation is explicitly opt-in."""
import argparse
import csv
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sys
import time


def parse_sample(line, fmt, column, key):
    if fmt == 'float':
        value = float(line.strip())
    elif fmt == 'csv':
        value = float(next(csv.reader([line]))[column])
    else:
        value = float(json.loads(line)[key])
    if not math.isfinite(value):
        raise ValueError('non-finite detector value')
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--list-ports', action='store_true')
    p.add_argument('--simulate', action='store_true', help='clearly labelled synthetic detector data')
    p.add_argument('--port')
    p.add_argument('--baud', type=int, help='required for hardware; inspect firmware first')
    p.add_argument('--format', choices=['float', 'csv', 'json'], help='required for hardware')
    p.add_argument('--column', type=int, default=0, help='zero-based CSV detector column')
    p.add_argument('--key', default='value', help='JSON detector field')
    p.add_argument('--output', type=Path)
    p.add_argument('--seconds', type=float, default=0, help='0 means until interrupted')
    p.add_argument('--rate', type=float, default=20, help='simulation samples/second')
    p.add_argument('--window', type=int, default=1000, help='maximum plot samples retained')
    p.add_argument('--headless', action='store_true', help='render a PNG without a display')
    p.add_argument('--plot-file', type=Path)
    p.add_argument('--no-plot', action='store_true', help='CSV logging only; no Matplotlib required')
    a = p.parse_args()
    if a.list_ports:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        for port in ports:
            print(f'{port.device}\t{port.description}\t{port.hwid}')
        if not ports:
            print('No serial ports visible in this session.')
        return 0
    if a.window < 2 or a.rate <= 0 or a.seconds < 0 or a.column < 0:
        p.error('window must be >=2, rate >0, seconds >=0, column >=0')
    if a.simulate and (a.port or a.baud or a.format):
        p.error('simulation does not take serial settings')
    if not a.simulate and not (a.port and a.baud and a.format):
        p.error('hardware requires explicit --port, --baud and --format after firmware inspection')
    if a.baud is not None and a.baud <= 0:
        p.error('baud must be positive')
    if (a.headless or a.no_plot) and a.seconds == 0:
        p.error('--headless requires a finite --seconds duration')
    if not a.no_plot:
        import matplotlib
        if a.headless:
            matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    mode = 'SIMULATED' if a.simulate else 'HARDWARE'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = a.output or Path('data') / f'{mode.lower()}-{stamp}.csv'
    plot_file = a.plot_file or output.with_suffix('.png')
    output.parent.mkdir(parents=True, exist_ok=True)
    xs, ys = deque(maxlen=a.window), deque(maxlen=a.window)
    if not a.no_plot:
        fig, ax = plt.subplots()
        curve, = ax.plot([], [], lw=1)
        ax.set(title=f'{mode} detector data — arbitrary units', xlabel='Elapsed time (s)', ylabel='Detector value (uncalibrated)')
        if not a.headless:
            plt.show(block=False)
    connection = None
    count = bad = 0
    start = time.monotonic()
    next_plot = start
    next_flush = start + 1
    print(f'{mode}: logging to {output}', flush=True)
    try:
        if not a.simulate:
            import serial
            # Opening a port can reset some ESP32 boards even without a write.
            connection = serial.Serial(port=None, baudrate=a.baud, timeout=0.1)
            connection.dtr = False
            connection.rts = False
            connection.port = a.port
            connection.open()
        with output.open('x', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp_utc', 'elapsed_s', 'mode', 'detector_value'])
            while True:
                now = time.monotonic()
                elapsed = now - start
                if a.seconds and elapsed >= a.seconds:
                    break
                if not a.no_plot and not a.headless and not plt.fignum_exists(fig.number):
                    break
                if a.simulate:
                    value = 1 + .25 * math.cos(4 * elapsed) + random.gauss(0, .015)
                    time.sleep(1 / a.rate)
                else:
                    raw = connection.read_until(b'\n', size=4096)
                    if not raw:
                        plt.pause(.001) if not a.no_plot and not a.headless else None
                        continue
                    try:
                        if not raw.endswith(b'\n'):
                            # Drain an oversized/incomplete frame before trying again.
                            bad += 1
                            while raw and not raw.endswith(b'\n'):
                                raw = connection.read_until(b'\n', size=4096)
                                if a.seconds and time.monotonic() - start >= a.seconds:
                                    break
                            continue
                        value = parse_sample(raw.decode('utf-8'), a.format, a.column, a.key)
                    except (ValueError, IndexError, KeyError, TypeError, UnicodeError, OverflowError):
                        bad += 1
                        if bad == 1 or bad % 100 == 0:
                            print(f'Skipped {bad} malformed records', file=sys.stderr)
                        if not a.no_plot and not a.headless:
                            plt.pause(.001)
                        continue
                elapsed = time.monotonic() - start
                writer.writerow([datetime.now(timezone.utc).isoformat(), f'{elapsed:.6f}', mode, value])
                count += 1
                xs.append(elapsed)
                ys.append(value)
                now = time.monotonic()
                if now >= next_flush:
                    f.flush()
                    next_flush = now + 1
                if not a.no_plot and now >= next_plot:
                    curve.set_data(xs, ys)
                    ax.relim()
                    ax.autoscale_view()
                    if not a.headless:
                        fig.canvas.draw_idle()
                        plt.pause(.001)
                    next_plot = now + .2
    except KeyboardInterrupt:
        pass
    finally:
        if connection is not None:
            connection.close()
        if not a.no_plot:
            curve.set_data(xs, ys)
            ax.relim()
            ax.autoscale_view()
            plot_file.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(plot_file, dpi=120)
            plt.close(fig)
        print(f'{mode}: {count} samples, {bad} skipped; plot: {plot_file if not a.no_plot else 'disabled'}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ModuleNotFoundError as exc:
        print(f'Missing dependency: {exc.name}. See README.md setup instructions; --no-plot supports logging without Matplotlib.', file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)
