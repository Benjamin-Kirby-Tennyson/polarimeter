"""Python side of shared golden vectors; does NOT execute/compile target C++."""
import json
from pathlib import Path
import unittest

from polarimeter_protocol import decode_frame,ProtocolError
from firmware.tools.generate_vectors import header

ROOT=Path(__file__).resolve().parents[1]


class SharedVectorTests(unittest.TestCase):
    def test_host_strict_json_and_exact_generated_target_fixture_bytes(self):
        rows=json.loads((ROOT/'firmware/shared/vectors.json').read_text())
        for v in rows:
            with self.subTest(name=v['name']):
                if v['host_json_ok']:
                    self.assertEqual(decode_frame(bytes.fromhex(v['hex']))['mode'],'SIMULATED')
                else:
                    with self.assertRaises(ProtocolError):decode_frame(bytes.fromhex(v['hex']))
        self.assertEqual(header(rows),(ROOT/'firmware/shared/vectors.hpp').read_text())
