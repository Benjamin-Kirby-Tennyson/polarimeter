"""Bounded receive-only serial sessions; physical USB access is hard-locked.

Importing this module does not import pyserial or access devices. Discovery
only enumerates metadata. Framing is supplied explicitly and describes mock
traffic until the actual firmware is inspected. No motor/stop/write API exists.
"""
from collections import deque
from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
import time
from typing import Protocol

from polarimeter_core import HARDWARE_LOCKED, SIMULATED


LOCK_REASON = ("Physical serial connection disabled: firmware protocol, encoder "
               "calibration and safe stop behavior are unverified; opening may "
               "reset the ESP32")


class HardwareLockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class USBDevice:
    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    location: str | None
    by_id: tuple[str, ...] = ()
    by_path: tuple[str, ...] = ()


def discover_usb_devices():
    """Enumerate USB serial metadata/symlinks without opening any port.

    No selection by VID/PID alone: CP2102 is a bridge, not firmware identity.
    An empty result can mean that this session hides host device nodes.
    """
    from serial.tools import list_ports

    def links(directory, device):
        return tuple(sorted(str(p) for p in Path(directory).glob("*")
                            if p.is_symlink() and p.resolve() == Path(device).resolve()))

    return tuple(USBDevice(p.device, p.vid, p.pid, p.serial_number, p.location,
                           links("/dev/serial/by-id", p.device),
                           links("/dev/serial/by-path", p.device))
                 for p in sorted(list_ports.comports(), key=lambda p: p.device)
                 if p.vid is not None and p.pid is not None)


class ReceiveBackend(Protocol):
    """Single-owner API: connect/close and bounded, nonblocking byte reads.

    A future physical implementation must run outside Tk callbacks. Closing
    this transport is not a motor stop. Backends must never reconnect inside
    read(), substitute simulation, or transmit data.
    """
    @property
    def mode(self) -> str: ...
    def connect(self) -> None: ...
    def read(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class USBSerialBackend:
    """Discovery identity only; no settings, Serial object or unlock switch.

    Physical I/O is deliberately deferred until a reviewed firmware-specific
    adapter can establish reset safety, UART settings, framing and stop rules.
    """
    mode = HARDWARE_LOCKED

    def __init__(self, device: USBDevice):
        self.device = device

    def connect(self):
        raise HardwareLockedError(LOCK_REASON)

    def read(self, size):
        raise HardwareLockedError(LOCK_REASON)

    def close(self):
        pass  # No device has been opened. This does not stop a motor.


def _positive_int(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 1..{maximum}")


class MockSerialBackend:
    """Explicitly SIMULATED bytes with bounded storage and unplug injection."""
    mode = SIMULATED

    def __init__(self, *, capacity=65536):
        _positive_int(capacity, "Mock capacity", 1048576)
        self.capacity = capacity
        self._buffer = bytearray()
        self.connected = False
        self.present = True

    def feed(self, data):
        if not isinstance(data, bytes):
            raise TypeError("Mock input must be bytes")
        if len(self._buffer) + len(data) > self.capacity:
            raise BufferError("SIMULATED input buffer full")
        self._buffer.extend(data)

    def connect(self):
        if not self.present:
            raise OSError("SIMULATED USB device absent")
        self.connected = True

    def read(self, size):
        _positive_int(size, "Read size", 65536)
        if not self.connected or not self.present:
            raise OSError("SIMULATED USB disconnected")
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    def unplug(self):
        self.present = False

    def close(self):
        self.connected = False
        self._buffer.clear()


@dataclass(frozen=True)
class Framing:
    """Explicit mock delimiter; maximum payload excludes the delimiter.

    Payloads remain opaque bytes: no detector values, encoder counts, units,
    checksums or acknowledgments are inferred here.
    """
    delimiter: bytes
    max_payload: int = 4096

    def __post_init__(self):
        if not isinstance(self.delimiter, bytes) or not 1 <= len(self.delimiter) <= 16:
            raise ValueError("Delimiter must contain 1..16 bytes")
        _positive_int(self.max_payload, "Maximum payload", 65536)


class State(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    STALE = "STALE"
    FAULT = "FAULT"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class Frame:
    mode: str
    session: int
    sequence: int
    received_at: float  # Host monotonic time; not device measurement time.
    payload: bytes


@dataclass(frozen=True)
class Status:
    mode: str
    state: State
    detail: str
    changed_at: float


class SerialConnection:
    """Caller-polled receive session; one bounded read per poll, no threads.

    stale_after/fault_after refer to time without a complete framed message,
    not proof of firmware health. Explicit connect() is required after faults;
    no automatic retries, handshakes or fallback. Queue overflow, bad backend
    behavior and oversized frames fault closed instead of losing samples.
    This layer never claims an instrument handshake or a safe motor stop.
    """

    def __init__(self, backend: ReceiveBackend, framing: Framing, *,
                 stale_after=2.0, fault_after=10.0, read_size=4096,
                 queue_size=128, event_size=32, clock=time.monotonic):
        if (not math.isfinite(stale_after) or not math.isfinite(fault_after)
                or not 0 < stale_after < fault_after):
            raise ValueError("Require finite 0 < stale_after < fault_after")
        for value, name, maximum in ((read_size, "Read size", 65536),
                                     (queue_size, "Queue size", 4096),
                                     (event_size, "Event size", 1024)):
            _positive_int(value, name, maximum)
        if backend.mode not in (SIMULATED, HARDWARE_LOCKED):
            raise ValueError("Unverified hardware backends are not supported")
        self.backend, self.framing = backend, framing
        self.mode = backend.mode
        self.stale_after, self.fault_after = stale_after, fault_after
        self.read_size, self.queue_size = read_size, queue_size
        self.clock = clock
        self.frames = deque()
        self.events = deque(maxlen=event_size)
        self._partial = bytearray()
        self._close_pending = False
        self.session = self.sequence = 0
        self.last_received_at = self.last_frame_at = None
        self._connected_at = None
        self.status = Status(self.mode, State.DISCONNECTED, "Not connected", clock())
        self.events.append(self.status)

    def _status(self, state, detail):
        if (self.status.state, self.status.detail) != (state, detail):
            self.status = Status(self.mode, state, detail, self.clock())
            self.events.append(self.status)

    def _clear(self):
        self.frames.clear()
        self._partial.clear()

    def _close(self):
        try:
            self.backend.close()
        except Exception as exc:
            self._close_pending = True
            return f"; close failed: {type(exc).__name__}: {exc}"
        self._close_pending = False
        return ""

    def _fault(self, detail):
        self._clear()
        self._status(State.FAULT, detail + self._close())

    def connect(self):
        if self.status.state in (State.CONNECTED, State.STALE):
            raise RuntimeError("Disconnect before connecting again")
        if self._close_pending:
            raise RuntimeError("Close failed; retry disconnect before reconnecting")
        self._clear()
        self.last_received_at = self.last_frame_at = None
        if self.mode == HARDWARE_LOCKED:
            self._status(State.LOCKED, LOCK_REASON)
            return False
        try:
            self.backend.connect()
        except HardwareLockedError as exc:
            self._status(State.LOCKED, str(exc))
            return False
        except Exception as exc:
            self._fault(f"Connect failed: {type(exc).__name__}: {exc}")
            return False
        self.session += 1
        self.sequence = 0
        self._connected_at = self.clock()
        self._status(State.CONNECTED, "Transport connected; firmware not verified")
        return True

    def disconnect(self):
        self._clear()
        error = self._close()
        self._status(State.FAULT if error else State.DISCONNECTED,
                     "Local transport closed; no motor stop sent" if not error else error[2:])

    def _receive(self, data, now):
        self._partial.extend(data)
        delimiter = self.framing.delimiter
        while True:
            end = self._partial.find(delimiter)
            if end < 0:
                # A trailing delimiter prefix is not part of the payload.
                prefix = 0
                for length in range(1, len(delimiter)):
                    if self._partial.endswith(delimiter[:length]):
                        prefix = length
                if len(self._partial) - prefix > self.framing.max_payload:
                    raise ValueError("Oversized serial frame")
                return
            if end > self.framing.max_payload:
                raise ValueError("Oversized serial frame")
            if len(self.frames) >= self.queue_size:
                raise BufferError("Receive queue full; consume frames before polling")
            payload = bytes(self._partial[:end])
            del self._partial[:end + len(delimiter)]
            self.sequence += 1
            self.frames.append(Frame(self.mode, self.session, self.sequence, now, payload))
            self.last_frame_at = now

    def poll(self):
        if self.status.state not in (State.CONNECTED, State.STALE):
            return self.status
        now = self.clock()
        # Check before reading so late bytes cannot hide a missed deadline.
        reference = self.last_frame_at if self.last_frame_at is not None else self._connected_at
        if now - reference >= self.fault_after:
            self._fault("Complete-frame timeout; device health unknown, no motor stop sent")
            return self.status
        try:
            data = self.backend.read(self.read_size)
            if not isinstance(data, bytes) or len(data) > self.read_size:
                raise ValueError("Backend violated bounded byte-read contract")
            if data:
                self.last_received_at = now
                self._receive(data, now)
        except Exception as exc:
            self._fault(f"Receive failed: {type(exc).__name__}: {exc}")
            return self.status
        reference = self.last_frame_at if self.last_frame_at is not None else self._connected_at
        if now - reference >= self.stale_after:
            self._status(State.STALE, "No recent complete frame; device health unknown")
        else:
            self._status(State.CONNECTED, "Transport connected; firmware not verified")
        return self.status

    def take_frame(self):
        return self.frames.popleft() if self.frames else None
