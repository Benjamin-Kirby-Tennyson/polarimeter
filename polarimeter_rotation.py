"""SIMULATED rotation profile and in-memory framed host; no serial/GPIO access.

All demo settings are invented fixtures, not TSINY parameters or a protocol for
the installed ESP32. Both endpoints share an explicit virtual microsecond clock.
An eventual physical transport needs a separate clock-mapping/safety review.
"""
from collections import OrderedDict, deque
import itertools

from polarimeter_controller import (OfflineController, Readiness, SimulatedReference,
                                   Timing, decimal, identity, number, uint)
from polarimeter_protocol import FrameDecoder, FrameError, FrameMessage, encode_frame
from polarimeter_simulation import (LoopParameters, MotorPlant, PIParameters,
                                    PlantParameters, SimulationHarness, SpeedPI)


def simulated_demo():
    """Explicit demo profile only; zero physical configuration is supplied."""
    ids = itertools.count(100)
    controller = OfflineController(
        timing=Timing(200000, 200000, 50000, 20000, 60000000),
        readiness=Readiness(.1, 10000, 5e6, 20000), boot_id="1" * 32,
        id_factory=lambda: f"{next(ids):032x}")
    plant = MotorPlant(PlantParameters(180, .12, 36000, 1.5, 1, .02, 0, 5, 0, .02),
                       continuity_id="a" * 32, seed=12)
    pi = SpeedPI(PIParameters(.012, .08, .7, .8))
    loop = LoopParameters(2, .08, .2, 200000, 50000, 200, 1, .7, .1, 500000, 500000)
    config = dict(config_id="2" * 32, calibration_id="3" * 32, counts_per_qwp_rev=54000,
                  max_speed_deg_s=60, max_acceleration_deg_s2=100,
                  min_angle_deg=-360, max_angle_deg=1800)
    plan = dict(end_deg=1080, speed_deg_s=30, acceleration_deg_s2=60, duration_us="45000000",
                plateau_start_deg=180, plateau_end_deg=900, plateau_speed_band_deg_s=1)
    return SimulationHarness(controller, plant, pi, loop), config, plan


class MemoryRotationLink:
    """Bounded in-memory transport and device polling, including in silence.

    Fault-injection switches affect only this simulation. Loss of a connection
    does not halt device polling; its lease can expire independently of the host.
    """
    mode = "SIMULATED"

    def __init__(self, harness, *, permit_simulated_reference=False):
        if type(harness) is not SimulationHarness or type(permit_simulated_reference) is not bool:
            raise TypeError("Explicit simulated harness/reference policy required")
        self.harness = harness
        self.permit_reference = permit_simulated_reference
        self.tx = deque()
        self.pending_rx = deque()
        self.priority_stop = None
        self.parser = FrameDecoder(frame_timeout_s=.05, preserve_payload=True)
        self.sequence = 0
        self.connected = True
        self.drop_next = self.corrupt_next = self.duplicate_next = False
        self.suppress_responses = self.drop_heartbeats = False
        self.delay_us = 0
        self.latest = None

    def send(self, frame, *, priority=False):
        if not isinstance(frame, bytes) or len(frame) > 1050:
            raise ValueError("Bounded bytes frame required")
        if not self.connected:
            raise OSError("SIMULATED link disconnected")
        if priority:
            if FrameMessage(frame).message.get("type") != "STOP":
                raise ValueError("Priority slot is for STOP only")
            self.priority_stop = frame
        elif len(self.tx) >= 8:
            self.harness.controller._fault("RX_OVERFLOW")
            raise BufferError("SIMULATED request queue full")
        else:
            self.tx.append(frame)

    def poll(self, now_us):
        uint(now_us)
        packets = []
        if self.connected:
            if self.priority_stop is not None:
                packets.append(self.priority_stop)
                self.priority_stop = None
            packets.extend(self.tx)
        self.tx.clear()
        events = list(self.parser.feed(b"", now=now_us / 1e6))
        for frame in packets:
            if self.drop_heartbeats:
                try:
                    if FrameMessage(frame).message.get("type") == "HEARTBEAT":
                        continue
                except ValueError:
                    pass
            for i in range(0, len(frame), 256):
                events.extend(self.parser.feed(frame[i:i + 256], now=now_us / 1e6))
        result = self.harness.step(now_us, events)
        c = self.harness.controller
        if (self.permit_reference and c.state == "CONFIGURED" and c._reference is None
                and c._stationary()):
            # Explicit simulated commissioning of the known invented zero plant.
            c.establish_reference(SimulatedReference("6" * 32, c.config["calibration_id"], 0, 0, 1))
        self.latest = result
        self.sequence += 1
        terminal = result["terminal"]
        packet = dict(v=1, mode=self.mode, type="STATUS", boot_id=c.boot_id, session_id=c.session_id,
            seq=str(self.sequence), time_us=str(now_us), state=c.state, active=c.output.active,
            duty=c.output.duty, angle=c.snapshot()["angle_deg"],
            velocity=None if c.feedback is None else c.feedback.velocity_deg_s,
            count=None if c.feedback is None or c.feedback.count is None else str(c.feedback.count),
            ref=None if c._reference is None else c._reference.epoch,
            challenge=None if c.challenge is None else c.challenge[0], generation=str(c.arm_generation),
            ready=c._stationary(), ack=[[r["request_type"], r["request_id"], r["outcome"]] for r in result["replies"]],
            terminal=None if terminal is None else [terminal["request_id"], terminal["outcome"], terminal["session_id"]],
            motion=c.motion_request_id, kind=c.motion_kind, faults=sorted(c.faults),
            truth=result["optical_fast_axis_truth_deg"],
            nonce=next((r.get("host_nonce") for r in result["replies"] if r["request_type"] == "HELLO"), None))
        encoded = encode_frame(packet)
        if self.drop_next or self.suppress_responses or not self.connected:
            self.drop_next = False
            return None
        if self.corrupt_next:
            encoded = encoded[:-2] + (b"0" if encoded[-2:-1] != b"0" else b"1") + b"\n"
            self.corrupt_next = False
        if len(self.pending_rx) >= 32:
            c._fault("TX_OVERFLOW")
            self.pending_rx.clear()
            return None
        self.pending_rx.append((now_us + uint(self.delay_us), encoded))
        if self.duplicate_next and len(self.pending_rx) < 32:
            self.pending_rx.append((now_us + self.delay_us, encoded))
            self.duplicate_next = False
        if self.pending_rx and self.pending_rx[0][0] <= now_us:
            return self.pending_rx.popleft()[1]
        return None


class RotationHost:
    """Transport-independent host logic with no transport-opening capability.

    The endpoint must supply framed poll/send and the explicitly shared simulated
    clock contract. UNKNOWN is latched; a late packet cannot silently restart it.
    """
    mode = "SIMULATED"
    fields = set("v mode type boot_id session_id seq time_us state active duty angle velocity count ref challenge generation ready ack terminal motion kind faults truth nonce".split())

    def __init__(self, transport, config, plan):
        if getattr(transport, "mode", None) != self.mode:
            raise ValueError("Only an explicit SIMULATED transport is supported")
        self.transport, self.config, self.plan = transport, dict(config), dict(plan)
        self.phase = "IDLE"
        self.latest = None
        self.boot_id = self.session_id = None
        self.nonce = "b" * 32
        self.request = self.heartbeat = self._sequence = 0
        self.pending = OrderedDict()
        self._seen = OrderedDict()
        self._acked = deque(maxlen=16)
        self._last_rx = self._last_hb = self._started = None
        self.motion_id = None
        self.accepted_motion = False
        self.terminal = None
        self.cancelled = self.failed = False
        self._device_time = None
        self.detail = "SIMULATED • ready for an explicit finite scan"

    def _send(self, kind, now, **extra):
        if len(self.pending) >= 8:
            raise BufferError("SIMULATED host pending queue full")
        if kind == "HEARTBEAT":
            self.heartbeat += 1
            rid = str(self.heartbeat)
        else:
            self.request += 1
            rid = str(self.request)
        m = dict(v=1, mode=self.mode, type=kind, request_id=rid,
                 boot_id=self.boot_id, session_id=self.session_id)
        if kind == "HELLO":
            m.update(boot_id=None, session_id=None, host_nonce=self.nonce)
        elif kind not in ("STOP", "GET_STATUS"):
            m["not_after_us"] = str(now + 150000)
        m.update(extra)
        frame = encode_frame(m)
        self.transport.send(frame, priority=kind == "STOP")
        if kind != "HEARTBEAT":
            self.pending[(kind, rid)] = [frame, now, now + 150000, False]
        return rid

    def start(self, now_us):
        if self.phase != "IDLE":
            raise RuntimeError("Create a new explicit simulation session for another scan")
        self._started = uint(now_us)
        self.phase = "CONNECTING"
        self._send("HELLO", now_us)

    def cancel(self, now_us):
        self.cancelled = True
        self.terminal = None
        self.phase = "UNKNOWN" if self.failed else "CANCELLING"
        self.pending.clear()
        try:
            self._send("STOP", uint(now_us))
        except (OSError, ValueError, BufferError):
            self.failed = True
            self.phase = "UNKNOWN"
        if not self.failed:
            self.detail = "SIMULATED stop requested • mechanical stopping is not confirmed"

    def _unknown(self, now, reason):
        if not self.failed:
            self.failed = True
            self.cancel(now)
        self.phase = "UNKNOWN"
        self.latest = self.terminal = None
        self.detail = "UNKNOWN • " + reason

    def _validate(self, event, now):
        p = event.message
        if set(p) != self.fields or type(p["v"]) is not int or p["v"] != 1 or p["mode"] != self.mode or p["type"] != "STATUS":
            raise ValueError("Invalid response envelope")
        identity(p["boot_id"])
        for field in ("session_id", "ref", "challenge", "nonce"):
            if p[field] is not None:
                identity(p[field])
        seq, timestamp = decimal(p["seq"], positive=True), decimal(p["time_us"])
        decimal(p["generation"])
        if not timestamp <= now or now - timestamp > 100000:
            raise ValueError("Expired/future simulated status")
        if p["state"] not in ("DISARMED", "CONFIGURED", "ARMED", "RUNNING", "STOPPED", "FAULT"):
            raise ValueError("Unknown controller state")
        if type(p["active"]) is not bool or type(p["ready"]) is not bool:
            raise ValueError("Invalid response booleans")
        if not -1 <= number(p["duty"]) <= 1 or (not p["active"] and p["duty"] != 0):
            raise ValueError("Inconsistent output status")
        if p["active"] != (p["state"] == "RUNNING"):
            raise ValueError("State/output contradiction")
        for key in ("angle", "velocity", "truth"):
            if p[key] is not None:
                number(p[key])
        if p["count"] is not None:
            value = p["count"]
            if not isinstance(value, str) or not value or value in ("-0", "+0") or str(int(value)) != value or not -(2**63) <= int(value) < 2**63:
                raise ValueError("Invalid raw count string")
        if p["motion"] is not None:
            decimal(p["motion"], positive=True)
        if p["kind"] not in (None, "MOVE_TO", "SCAN"):
            raise ValueError("Unknown motion kind")
        if not isinstance(p["faults"], list) or len(p["faults"]) > 16 or any(not isinstance(f, str) or len(f) > 32 for f in p["faults"]):
            raise ValueError("Invalid faults")
        if not isinstance(p["ack"], list) or len(p["ack"]) > 8:
            raise ValueError("Invalid acknowledgments")
        for ack in p["ack"]:
            if (not isinstance(ack, list) or len(ack) != 3 or not isinstance(ack[0], str) or ack[0] not in OfflineController._extra
                    or not isinstance(ack[2], str) or len(ack[2]) > 48):
                raise ValueError("Malformed acknowledgment")
            decimal(ack[1], positive=True)
        t = p["terminal"]
        if t is not None:
            if not isinstance(t, list) or len(t) != 3 or t[1] not in ("COMPLETED", "CANCELLED", "FAULT"):
                raise ValueError("Malformed terminal event")
            decimal(t[0], positive=True)
            identity(t[2])
            if t[1] == "COMPLETED" and (p["state"] != "STOPPED" or p["active"]):
                raise ValueError("Completion/state contradiction")
        return p, seq

    def _receive(self, frame, now):
        event = FrameMessage(frame)
        p, seq = self._validate(event, now)
        if self.failed:
            return
        if seq <= self._sequence:
            if seq in self._seen and self._seen[seq] != event.payload:
                raise ValueError("Conflicting duplicate response")
            return
        if self.boot_id is None:
            hello = any(a[0] == "HELLO" and a[2] == "HELLO_REPLY" and (a[0], a[1]) in self.pending for a in p["ack"])
            stop = self.cancelled and any(a[0] == "STOP" for a in p["ack"])
            if not stop and (not hello or p["nonce"] != self.nonce or p["session_id"] is None):
                return
            self.boot_id, self.session_id = p["boot_id"], p["session_id"]
        elif p["boot_id"] != self.boot_id or p["session_id"] != self.session_id:
            raise ValueError("Unexpected boot/session change")
        self._sequence = seq
        if self._device_time is not None and decimal(p["time_us"]) < self._device_time:
            raise ValueError("Device clock moved backwards")
        self._device_time = decimal(p["time_us"])
        self._seen[seq] = event.payload
        while len(self._seen) > 8:
            self._seen.popitem(last=False)
        self._last_rx = now
        self.latest = p
        for kind, rid, outcome in p["ack"]:
            key = (kind, rid)
            if kind == "HEARTBEAT":
                continue
            if key not in self.pending:
                if key in self._acked:
                    continue
                if self.cancelled and kind != "STOP":
                    continue
                raise ValueError("Unsolicited acknowledgment")
            if now >= self.pending[key][2]:
                raise ValueError("Late request acknowledgment")
            self.pending.pop(key)
            self._acked.append(key)
            if self.failed:
                continue
            if kind == "STOP":
                if outcome != "INHIBIT_REQUESTED":
                    raise ValueError("STOP not acknowledged")
                self.phase = "CANCELLED"
            elif self.cancelled:
                continue
            elif outcome not in (("HELLO_REPLY",) if kind == "HELLO" else ("ACCEPTED", "RETRY:ACCEPTED")):
                raise ValueError("Command rejected: " + outcome)
            else:
                self.phase = {"HELLO": "WAIT_READY", "CONFIGURE": "WAIT_REFERENCE",
                              "GET_STATUS": "WAIT_CHALLENGE", "ARM": "WAIT_SCAN", "SCAN": "SCANNING"}[kind]
                if kind == "SCAN":
                    self.accepted_motion = True
                    self.detail = "Finite simulated scan running; encoder and optical truth are separate"
        if p["faults"] and not self.failed:
            self.phase = "FAULT"
            self.detail = "SIMULATED fault: " + ", ".join(p["faults"])
        if p["terminal"] is not None and not self.failed:
            rid, outcome, session = p["terminal"]
            if rid != self.motion_id or session != self.session_id:
                raise ValueError("Terminal event belongs to another motion")
            if self.accepted_motion and self.terminal is None:
                self.terminal = tuple(p["terminal"])
                self.phase = "COMPLETED" if outcome == "COMPLETED" else "FAULT" if outcome == "FAULT" else "CANCELLED"
                self.detail = "Finite simulated motion " + outcome.lower() + "; output inhibition does not prove mechanical stopping"

    def tick(self, now_us):
        now = uint(now_us)
        try:
            frame = self.transport.poll(now)
            if frame is not None:
                self._receive(frame, now)
            if self.failed:
                return
            if self._last_rx is not None and now - self._last_rx >= 100000:
                self._unknown(now, "status lost; stopping state is unknown")
                return
            for key, pending in tuple(self.pending.items()):
                frame, sent, deadline, retried = pending
                if now >= deadline:
                    self._unknown(now, "request acknowledgment timed out")
                    return
                if not retried and now - sent >= 50000:
                    self.transport.send(frame, priority=key[0] == "STOP")
                    pending[3] = True
            if self.cancelled or self.phase in ("IDLE", "COMPLETED", "CANCELLED", "FAULT"):
                return
            p = self.latest
            if p is None:
                return
            if self.phase == "WAIT_READY" and p["ready"]:
                self._send("CONFIGURE", now, config_generation="0", config=self.config)
                self.phase = "CONFIGURING"
            elif self.phase == "WAIT_REFERENCE" and p["ref"] is not None:
                self._send("GET_STATUS", now)
                self.phase = "ASK_CHALLENGE"
            elif self.phase == "WAIT_CHALLENGE" and p["challenge"] is not None:
                self._send("ARM", now, config_id=self.config["config_id"], challenge=p["challenge"])
                self.phase = "ARMING"
            elif self.phase == "WAIT_SCAN":
                self.motion_id = self._send("SCAN", now, arm_generation=p["generation"], **self.plan)
                self.phase = "SCAN_SENT"
            if p["state"] in ("ARMED", "RUNNING") and (self._last_hb is None or now - self._last_hb >= 50000):
                self._send("HEARTBEAT", now, request_id=str(self.heartbeat + 1),
                           heartbeat_seq=str(self.heartbeat + 1), arm_generation=p["generation"],
                           not_after_us=str(now + 200000))
                self._last_hb = now
        except (ValueError, ArithmeticError, OSError, BufferError) as error:
            self._unknown(now, str(error))
