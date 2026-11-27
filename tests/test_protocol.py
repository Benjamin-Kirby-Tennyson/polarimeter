"""Offline framing tests; no board, serial port, command dispatch or GPIO."""
from pathlib import Path
import random
import subprocess
import sys
import unittest
import zlib

from polarimeter_protocol import (FrameDecoder, FrameError, MAX_CHUNK,
                                  MAX_FRAME, ProtocolError, decode_frame,
                                  encode_frame)


def raw_frame(payload, *, length=None):
    prefix = b"QWP1 " + str(len(payload) if length is None else length).encode() + b" " + payload
    return prefix + f" {zlib.crc32(prefix):08X}\n".encode()


class CodecTests(unittest.TestCase):
    def test_exact_empty_object_vector(self):
        # Independent CRC check and a complete literal frame below.
        self.assertEqual(zlib.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(encode_frame({}), b'QWP1 2 {} 71E17289\n')
        self.assertEqual(encode_frame({"mode": "SIMULATED", "type": "STOP"}),
                         b'QWP1 34 {"mode":"SIMULATED","type":"STOP"} FCCAB49A\n')
        self.assertEqual(decode_frame(raw_frame(b"{}")), {})
        self.assertEqual(encode_frame({}), raw_frame(b"{}"))

    def test_roundtrip_multibyte_escaped_newline_and_exact_integers(self):
        message = {"mode": "SIMULATED", "text": "λ\nQWP1 \r", "id": "18446744073709551615",
                   "counts": -(2**63), "nested": {"flags": [True, None]}}
        encoded = encode_frame(message)
        self.assertEqual(decode_frame(encoded), message)
        self.assertNotIn(b"\r", encoded)
        self.assertEqual(encoded.count(b"\n"), 1)
        self.assertEqual(decode_frame(raw_frame('{"text":"λ"}'.encode())), {"text": "λ"})

    def test_bad_crc_length_magic_and_trailers(self):
        valid = raw_frame(b"{}")
        candidates = [valid.replace(b"{}", b"[]"), valid[:-1], valid + b"\n",
                      valid[:-1] + b"\r\n", valid.replace(b"QWP1", b"QWP2"),
                      valid.replace(b" 2 ", b" 02 "), raw_frame(b"{}", length=3),
                      valid[:-10] + b" ABCDEFGH\n", b"QWP1 1025 ", b"x" * (MAX_FRAME + 1)]
        for frame in candidates:
            with self.subTest(frame=frame[:50]), self.assertRaises(ProtocolError):
                decode_frame(frame)

    def test_strict_json_rejects_duplicate_keys_constants_and_unknown_shapes(self):
        for payload in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}',
                        b'{"x":1e999}', b'{"x":9223372036854775808}',
                        b'{"x":"\\ud800"}', b'[]', b'null', b'{"x":"\xff"}',
                        b'{"x":"raw\nnewline"}', b'{"a":{"b":{"c":{"d":0}}}}',
                        b'{"x":[' + b'0,' * 127 + b'0]}'):
            with self.subTest(payload=payload[:45]), self.assertRaises(ProtocolError):
                decode_frame(raw_frame(payload))

    def test_encoder_rejects_invalid_unbounded_or_cyclic_objects(self):
        cyclic = {}
        cyclic["cycle"] = cyclic
        for obj in ([], {1: "key"}, {"x": float("nan")}, {"x": float("inf")},
                    {"x": 2**63}, {"x": "\ud800"}, {"x": object()},
                    {"x": "x" * 1025}, {"x": [0] * 129}, cyclic):
            with self.subTest(kind=type(obj)), self.assertRaises(ProtocolError):
                encode_frame(obj)

    def test_exact_payload_limit_and_byte_length(self):
        payload = b'{"x":"' + b'x' * 1016 + b'"}'
        self.assertEqual(len(payload), 1024)
        frame = raw_frame(payload)
        self.assertEqual(encode_frame(decode_frame(frame)), frame)
        with self.assertRaises(ProtocolError):
            decode_frame(raw_frame(payload + b' '))

    def test_import_and_codec_do_not_load_hardware_packages(self):
        code = ("import sys; import polarimeter_protocol as p; "
                "assert p.decode_frame(p.encode_frame({'mode':'SIMULATED'}))['mode']=='SIMULATED'; "
                "assert not {'serial','numpy','scipy','matplotlib'} & set(sys.modules)")
        result = subprocess.run([sys.executable, "-B", "-c", code],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class StreamTests(unittest.TestCase):
    def parser(self):
        return FrameDecoder(frame_timeout_s=1)  # Fixture only, not a hardware setting.

    def test_every_split_and_coalesced_messages_preserves_order(self):
        one = {"type": "STOP", "mode": "SIMULATED"}
        two = {"type": "STATUS", "mode": "SIMULATED"}
        frame = encode_frame(one)
        for split in range(len(frame) + 1):
            parser = self.parser()
            events = parser.feed(frame[:split], now=0) + parser.feed(frame[split:], now=.1)
            self.assertEqual(events, (one,))
        self.assertEqual(self.parser().feed(frame + encode_frame(two), now=0), (one, two))

    def test_timeout_at_exact_boundary_discards_through_lf(self):
        parser = self.parser()
        self.assertEqual(parser.feed(b'QWP1 80 {"type":', now=0), ())
        self.assertEqual(parser.feed(b'', now=.999), ())
        expired = parser.feed(b'', now=1)
        self.assertEqual(len(expired), 1)
        self.assertIsInstance(expired[0], FrameError)
        self.assertEqual(parser.feed(b'', now=2), ())
        stop = encode_frame({"type": "STOP"})
        # A STOP inside a rejected partial line is not promoted into a command.
        self.assertEqual(parser.feed(stop, now=2), ())
        self.assertEqual(parser.feed(stop, now=2), ({"type": "STOP"},))

    def test_oversize_and_cr_recover_only_after_line_boundary(self):
        for invalid in (b'x' * MAX_FRAME, b'QWP1 2 {}\r'):
            parser = self.parser()
            events = ()
            for offset in range(0, len(invalid), MAX_CHUNK):
                events += parser.feed(invalid[offset:offset + MAX_CHUNK], now=0)
                self.assertLessEqual(parser.buffered_bytes, MAX_FRAME)
            self.assertEqual(len(events), 1)
            self.assertIsInstance(events[0], FrameError)
            self.assertEqual(parser.buffered_bytes, 0)
            stop = encode_frame({"type": "STOP"})
            self.assertEqual(parser.feed(b'\n' + stop, now=0), ({"type": "STOP"},))

    def test_malformed_then_valid_frame_delivers_error_before_message(self):
        events = self.parser().feed(b'bad\n' + encode_frame({"type": "STOP"}), now=0)
        self.assertIsInstance(events[0], FrameError)
        self.assertEqual(events[1], {"type": "STOP"})

    def test_rejected_calls_do_not_advance_parser_or_timeout_clock(self):
        parser = self.parser()
        parser.feed(b'QW', now=2)
        for data, now, error in ((b'x' * 257, 100, ValueError), ('x', 2, TypeError),
                                 (b'', 1, ValueError), (b'', float('nan'), ValueError),
                                 (b'', True, ValueError)):
            with self.assertRaises(error):
                parser.feed(data, now=now)
            self.assertEqual(parser.buffered_bytes, 2)
        self.assertEqual(parser.feed(b'', now=2), ())

    def test_noisy_stream_is_bounded_and_recovers_for_final_valid_frame(self):
        parser = self.parser()
        rng = random.Random(19)
        for _ in range(200):
            chunk = bytes(rng.randrange(256) for _ in range(256))
            events = parser.feed(chunk, now=0)
            self.assertLessEqual(parser.buffered_bytes, MAX_FRAME)
            self.assertLessEqual(len(events), MAX_CHUNK + 1)
        parser.feed(b'\n', now=0)
        final = {"mode": "SIMULATED", "id": "123"}
        self.assertEqual(parser.feed(encode_frame(final), now=0), (final,))

    def test_timeout_configuration_is_explicit_and_finite(self):
        for value in (0, -1, float('nan'), float('inf'), True, '1'):
            with self.assertRaises(ValueError):
                FrameDecoder(frame_timeout_s=value)
