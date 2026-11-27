"""Review regressions: all scalar results, real time gaps and manifest integrity."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polarimeter_core import JonesModel
from polarimeter_optical import RotationLog
from polarimeter_stokes import analyze_csv,fit_records
from tests.test_stokes import records


class AnalysisIntegrityTests(unittest.TestCase):
    def test_nonfinite_scalar_dop_is_rejected(self):
        with patch('polarimeter_stokes.np.linalg.norm',return_value=float('inf')):
            with self.assertRaisesRegex(ValueError,'scalar'):
                fit_records(records(JonesModel()),zero_offset_deg=0,analyzer_deg=0,calibration_id='explicit-zero',period_us=20000)

    def test_time_gap_with_consecutive_ids_is_not_contiguous_coverage(self):
        rows=list(records(JonesModel()))
        for r in rows[360:]:
            r['mapped_start_us']+=20000;r['mapped_end_us']+=20000
        result=fit_records(rows,zero_offset_deg=0,analyzer_deg=0,calibration_id='explicit-zero',period_us=20000)
        self.assertEqual(result['temporal_gap_count'],1)
        self.assertIn('TEMPORAL_GAPS_IN_VALID_EXPOSURES',result['flags'])
        self.assertLess(result['contiguous_midpoint_coverage_deg'],result['valid_midpoint_span_deg'])

    def test_manifest_full_identity_and_counts_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            log=RotationLog(folder,metadata={'experiment_id':'e','period_us':20000})
            rows=list(records(JonesModel()))
            for r in rows:log.write_sample(r)
            log.finish(phase='COMPLETED',detail='fixture',metrics={'valid_samples':720,'rejected_samples':0,
                'received_exposures':720,'expected_exposures':720,'missing_exposures':0,'pending_exposures':0})
            original=json.loads(log.manifest_path.read_text())
            settings=dict(zero_offset_deg=0,analyzer_deg=0,calibration_id='explicit-zero')
            path,result=analyze_csv(log.sample_path,**settings)
            self.assertEqual(result['analysis']['declared_period_us'],20000)
            self.assertEqual(result['observed_acquisition_identities'],original['acquisition_identities'])
            for change in ('boot','arm','calibration','rows','valid','total','identity-type','config-type'):
                m=json.loads(json.dumps(original))
                if change in ('boot','arm','calibration'):
                    key={'boot':'boot_id','arm':'arm_generation','calibration':'calibration_id'}[change]
                    m['acquisition_identities'][0][key]='wrong'
                elif change=='identity-type':m['acquisition_identities'][0]['boot_id']=[]
                elif change=='config-type':m['metadata']['config']=[]
                elif change=='rows':m['written_exposure_rows']=719
                elif change=='valid':m['metrics']['valid_samples']=719
                else:m['metrics']['expected_exposures']=721
                log.manifest_path.write_text(json.dumps(m))
                with self.subTest(change=change),self.assertRaisesRegex(ValueError,'Manifest'):
                    analyze_csv(log.sample_path,**settings)


class EncoderGapTests(unittest.TestCase):
    def test_new_sample_after_gap_requires_new_reference_epoch(self):
        from tests.simulation_fixtures import Scenario
        from polarimeter_controller import EncoderSample,SimulatedReference
        s=Scenario();c=s.controller
        now=s.now+c.timing.feedback_us
        c.cycle(now_us=now,feedback=EncoderSample(now,0,'a'*32))
        self.assertEqual(c.state,'FAULT')
        self.assertIn('FEEDBACK',c.faults)
        self.assertIsNone(c._reference)
        for _ in range(8):
            now+=5000;c.cycle(now_us=now,feedback=EncoderSample(now,0,'a'*32))
        self.assertTrue(c._stationary())
        self.assertIsNone(c._reference)
        with self.assertRaises(ValueError):c.establish_reference(SimulatedReference('6'*32,'3'*32,0,0,1))
        c.establish_reference(SimulatedReference('7'*32,'3'*32,0,0,1))
        self.assertEqual(c._reference.epoch,'7'*32)
