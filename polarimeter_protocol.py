"""Offline proposed QWP1 framing only; no serial/GPIO imports or command dispatch.

This codec is not compatible with any identified installed firmware. Decoding
a JSON object does not validate a command or authorize motion. Physical serial
and motor adapters elsewhere in the application remain locked.
"""
from dataclasses import dataclass
import json
import math
import re
import zlib


MAX_PAYLOAD = 1024
MAX_FRAME = 1050
MAX_CHUNK = 256
MAX_DEPTH = 4


class ProtocolError(ValueError):
    """Invalid proposed frame, JSON shape or bounded-data requirement."""


@dataclass(frozen=True)
class FrameError:
    detail: str


@dataclass(frozen=True)
class FrameMessage:
    """Validated immutable original bytes; message access returns a fresh dict."""
    frame: bytes

    def __post_init__(self):
        decode_frame(self.frame)

    @property
    def payload(self):
        match = re.match(rb"QWP1 ([1-9][0-9]{0,3}) ", self.frame)
        return self.frame[match.end():match.end() + int(match.group(1))]

    @property
    def message(self):
        return decode_frame(self.frame)


def _validate(value, depth=1, budget=None):
    if budget is None:
        budget = [128]
    budget[0] -= 1
    if budget[0] < 0:
        raise ProtocolError("JSON exceeds 128 nodes including object keys")
    if depth > MAX_DEPTH:
        raise ProtocolError("JSON nesting exceeds four levels")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > MAX_PAYLOAD:
            raise ProtocolError("String exceeds payload bound")
        try:
            if len(value.encode("utf-8")) > MAX_PAYLOAD:
                raise ProtocolError("String exceeds payload byte bound")
        except UnicodeError as exc:
            raise ProtocolError("Invalid Unicode string") from exc
    elif isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise ProtocolError("JSON integer exceeds signed 64-bit range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("Nonfinite JSON number")
    elif isinstance(value, dict):
        if len(value) > 128:
            raise ProtocolError("Too many object fields")
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings")
            _validate(key, depth, budget)
            _validate(child, depth + 1, budget)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ProtocolError("Too many array elements")
        for child in value:
            _validate(child, depth + 1, budget)
    else:
        raise ProtocolError("Unsupported JSON value")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON object key")
        result[key] = value
    return result


def _constant(value):
    raise ProtocolError("Nonfinite JSON constant")


def encode_frame(message):
    """Return fixture bytes; does not send them anywhere or validate commands."""
    if not isinstance(message, dict):
        raise ProtocolError("Payload must be a JSON object")
    try:
        _validate(message)
        payload = json.dumps(message, ensure_ascii=True, allow_nan=False,
                             separators=(",", ":"), sort_keys=True).encode("ascii")
    except RecursionError as exc:
        raise ProtocolError("Cyclic or excessively nested JSON") from exc
    if not 1 <= len(payload) <= MAX_PAYLOAD:
        raise ProtocolError("Payload length outside 1..1024 bytes")
    covered = b"QWP1 " + str(len(payload)).encode("ascii") + b" " + payload
    return covered + f" {zlib.crc32(covered):08X}\n".encode("ascii")


def decode_frame(frame):
    """Validate one full bounded frame; command-schema validation is separate."""
    if not isinstance(frame, bytes):
        raise TypeError("Frame must be bytes")
    if not 1 <= len(frame) <= MAX_FRAME:
        raise ProtocolError("Frame length outside bounds")
    if not frame.endswith(b"\n") or b"\n" in frame[:-1] or b"\r" in frame:
        raise ProtocolError("Require exactly one terminal LF and no raw CR")
    match = re.match(rb"QWP1 ([1-9][0-9]{0,3}) ", frame)
    if match is None:
        raise ProtocolError("Invalid magic or canonical length")
    size = int(match.group(1))
    if size > MAX_PAYLOAD:
        raise ProtocolError("Payload exceeds 1024 bytes")
    end = match.end() + size
    if len(frame) != end + 10 or frame[end:end + 1] != b" ":
        raise ProtocolError("Payload length/trailer mismatch")
    checksum = frame[end + 1:end + 9]
    if re.fullmatch(rb"[0-9A-F]{8}", checksum) is None:
        raise ProtocolError("CRC must be eight uppercase hexadecimal digits")
    if int(checksum, 16) != zlib.crc32(frame[:end]):
        raise ProtocolError("CRC mismatch")
    try:
        result = json.loads(frame[match.end():end].decode("utf-8"),
                            object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(result, dict):
            raise ProtocolError("Payload must be a JSON object")
        _validate(result)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("Invalid UTF-8 or JSON payload") from exc
    return result


class FrameDecoder:
    """Bounded caller-polled offline stream parser with ordered error events.

    Explicit timeout is a fixture/review parameter, not a hardware default.
    Recover only at LF; never scan a rejected payload for embedded STOP/magic.
    Each feed processes <=256 bytes; retained line storage is <=1050 bytes.
    No queue or automatic retries. A controller must preflight the complete
    bounded batch for fault/expiry/STOP before motion-enabling mutations.
    """

    def __init__(self, *, frame_timeout_s, preserve_payload=False):
        if (isinstance(frame_timeout_s, bool) or not isinstance(frame_timeout_s, (int, float))
                or not math.isfinite(frame_timeout_s) or frame_timeout_s <= 0):
            raise ValueError("Require an explicit positive finite frame timeout")
        self.timeout = frame_timeout_s
        if not isinstance(preserve_payload, bool):
            raise ValueError("preserve_payload must be boolean")
        self.preserve_payload = preserve_payload
        self._line = bytearray()
        self._started = self._last_time = None
        self._discard = False

    @property
    def buffered_bytes(self):
        return len(self._line)

    def feed(self, chunk, *, now):
        if not isinstance(chunk, bytes):
            raise TypeError("Chunk must be bytes")
        if len(chunk) > MAX_CHUNK:
            raise ValueError("Chunk exceeds offline processing budget")
        if (isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now)
                or (self._last_time is not None and now < self._last_time)):
            raise ValueError("Require finite nondecreasing monotonic time")
        self._last_time = now
        events = []
        if self._started is not None and now - self._started >= self.timeout:
            self._line.clear()
            self._started = None
            self._discard = True
            events.append(FrameError("Partial frame timed out; discard through next LF"))
        for byte in chunk:
            if self._discard:
                if byte == 10:
                    self._discard = False
                continue
            if self._started is None:
                self._started = now
            self._line.append(byte)
            if byte == 10:
                try:
                    frame = bytes(self._line)
                    events.append(FrameMessage(frame) if self.preserve_payload else decode_frame(frame))
                except ProtocolError as exc:
                    events.append(FrameError(str(exc)))
                self._line.clear()
                self._started = None
            elif byte == 13 or len(self._line) >= MAX_FRAME:
                events.append(FrameError("Raw CR or overlong frame; discard through next LF"))
                self._line.clear()
                self._started = None
                self._discard = True
        return tuple(events)
