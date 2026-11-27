"""Python side of shared golden vectors; does NOT execute/compile target C++."""
import json
from pathlib import Path
import unittest

from polarimeter_protocol import decode_frame,ProtocolError
from firmware.tools.generate_vectors import header,fixtures
from firmware.tools.generate_traces import traces,cpp
from polarimeter_controller import OfflineController,Timing,Readiness
from polarimeter_protocol import FrameMessage

ROOT=Path(__file__).resolve().parents[1]


class SharedVectorTests(unittest.TestCase):
    def test_host_strict_json_and_exact_generated_target_fixture_bytes(self):
        rows=json.loads((ROOT/'firmware/shared/vectors.json').read_text())
        for v in rows:
            with self.subTest(name=v['name']):
                if v['host_json_ok']:
                    self.assertIsInstance(decode_frame(bytes.fromhex(v['hex'])),dict)
                else:
                    with self.assertRaises(ProtocolError):decode_frame(bytes.fromhex(v['hex']))
        checker=OfflineController(timing=Timing(200000,200000,50000,20000,20000000),
            readiness=Readiness(.1,10000,5e6,20000),boot_id='1'*32)
        for v in rows:
            try:checker._schema(FrameMessage(bytes.fromhex(v['hex'])));accepted=True
            except ValueError:accepted=False
            self.assertEqual(accepted,v['schema_ok'],v['name'])
        self.assertEqual(rows,fixtures())
        self.assertEqual(header(rows),(ROOT/'firmware/shared/vectors.hpp').read_text())

    def test_controller_reference_traces_are_reproducible(self):
        data=json.loads((ROOT/'firmware/shared/controller-traces.json').read_text())
        self.assertEqual(data,traces())
        self.assertEqual((ROOT/'firmware/shared/controller-traces.hpp').read_text(),cpp(data))
        self.assertEqual(len(data),38)
        self.assertEqual(sum(len(t['steps']) for t in data),472)

    def test_no_derived_state_injection_in_reference_traces(self):
        data=traces()
        for trace in data:
            self.assertNotIn('state',trace['initial'])
            for step in trace['steps']:
                self.assertNotIn('gates',step)
                self.assertTrue('sample' in step or 'reference' in step)
        rows=fixtures()
        self.assertTrue(all(not v['schema_ok'] for v in rows if 'delimiter' in v['name']))
