"""Independent simulated detector cadence, guarded fitting and immutable sidecar."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from polarimeter_optical import OpticalAcquisition,OpticalClock,RotationLog
from polarimeter_stokes import analyze_csv,fit_records
from polarimeter_core import JonesModel
from tests.test_stokes import records,fit


def acquisition():
    clock=OpticalClock(10000,50,100)
    return OpticalAcquisition(plan=dict(plateau_start_deg=180,plateau_end_deg=183,speed_deg_s=30,
        plateau_speed_band_deg_s=1,max_speed_deg_s=31),exposure_us=10000,period_us=20000,
        detector_clock=clock,clock_mapping=clock,experiment_id='e',config_id='c',calibration_id='k')


def packet(i):
    t=i*5000;angle=179.7+30*t/1e6
    return dict(mode='SIMULATED',seq=str(i+1),time_us=str(t),boot_id='b',session_id='s',generation='1',
        motion='5',ref='r',kind='SCAN',state='RUNNING',angle=angle,truth=angle+5,velocity=30,count=str(round(angle*150)))


def run_detector(dropped=(),*,delay=0,mapping=True,drop_detector=False):
    a=acquisition();out=[]
    if not mapping:a.mapping=None
    delayed=[]
    for i in range(36):
        p=packet(i);now=i*5000
        a.record_truth(now,p['truth'])
        if drop_detector and i==8:a.drop_next_exposure=True
        a.advance(now,dict(p,mechanical_truth_deg=p['angle']))
        if i not in dropped:delayed.append((now+delay,p))
        while delayed and delayed[0][0]<=now:a.observe(delayed.pop(0)[1])
        out.extend(a.deliver(now))
    return a,out


class DetectorCadenceTests(unittest.TestCase):
    def test_leading_trailing_interior_and_outage_do_not_erase_exposures(self):
        baseline,rows=run_detector()
        for lost in ((1,2,3,4,5),(19,20,21,22,23),(8,9,10),tuple(range(3,24))):
            a,result=run_detector(lost)
            self.assertEqual(a.expected,baseline.expected)
            self.assertEqual([r['sequence'] for r in result],[r['sequence'] for r in rows])
            self.assertEqual([r['intensity_au'] for r in result],[r['intensity_au'] for r in rows])
            self.assertEqual(a.valid+a.rejected,a.expected)
            self.assertTrue(any(r['quality']!='VALID' for r in result))
            self.assertTrue(all(r['intensity_au'] is not None for r in result))

    def test_delay_clock_failure_and_actual_detector_loss_are_distinct(self):
        base,rows=run_detector()
        for opts in (dict(delay=10000),dict(mapping=False)):
            a,result=run_detector(**opts)
            self.assertEqual(a.expected,base.expected)
            self.assertEqual([r['intensity_au'] for r in rows],[r['intensity_au'] for r in result])
        a,result=run_detector(drop_detector=True)
        self.assertEqual(a.expected,base.expected)
        self.assertEqual(a.missing,1)
        self.assertEqual(a.valid+a.rejected+a.missing,a.expected)

    def test_cadence_clock_reversal_and_missed_poll_fail_explicitly(self):
        a=acquisition();p=packet(4)
        a.advance(20000,dict(p,mechanical_truth_deg=p['angle']))
        with self.assertRaisesRegex(ValueError,'backwards'):a.advance(15000,dict(p,mechanical_truth_deg=p['angle']))
        a=acquisition()
        with self.assertRaisesRegex(ValueError,'at least once'):a.advance(100000,dict(p,mechanical_truth_deg=p['angle']))


class AnalysisWorkflowTests(unittest.TestCase):
    def test_bad_sequences_overlap_direction_extremes_and_small_coverage(self):
        for changes in ({'sequence':0},{'sequence':'01'},{'sequence':1.5},
                        {'estimated_start_deg':1e300},{'estimated_end_deg':float('inf')},
                        {'intensity_au':1e308},{'mapped_start_us':0}):
            rows=list(records(JonesModel()));rows[10].update(changes)
            with self.assertRaises(ValueError):fit(rows)
        rows=list(records(JonesModel()))
        rows[10]['estimated_end_deg']=rows[10]['estimated_start_deg']-1
        with self.assertRaises(ValueError):fit(rows)
        rows=list(records(JonesModel()))[:30]
        with self.assertRaisesRegex(ValueError,'90 degrees'):fit(rows)

    def test_zero_dop_covariance_and_offset_sensitivity(self):
        rows=list(records(JonesModel()))
        for r in rows:r['intensity_au']=.5
        result=fit(rows)
        self.assertIsNone(result['dop_standard_error'])
        self.assertIn('DOP_NEAR_ZERO_DELTA_INVALID',result['flags'])
        self.assertEqual(len(result['stokes_covariance']),4)
        model=JonesModel(ex=.8,ey=.6j)
        baseline=fit(records(model))
        shifted=fit(records(model),zero=.1)
        error=max(abs(a-b) for a,b in zip(baseline['normalized_stokes'],shifted['normalized_stokes']))
        self.assertGreater(error,.0001);self.assertLess(error,.02)
        reverse=[]
        for r in records(model):
            r=dict(r,estimated_start_deg=-r['estimated_start_deg'],estimated_end_deg=-r['estimated_end_deg'])
            # Independent model measurement for negative angular direction.
            r['intensity_au']=model.intensity((r['estimated_start_deg']+r['estimated_end_deg'])/2)
            reverse.append(r)
        self.assertEqual(fit(reverse)['direction'],-1)

    def test_nonideal_retardance_and_analyzer_error_are_model_experiments(self):
        import math
        baseline=fit(records(JonesModel(ex=.8,ey=.6j)))
        nonideal=fit(records(JonesModel(ex=.8,ey=.6j,retardance_rad=math.pi/2+.1)))
        wrong_analyzer=fit(records(JonesModel(ex=.8,ey=.6j,analyzer_deg=1)))
        self.assertNotEqual(baseline['normalized_stokes'],nonideal['normalized_stokes'])
        self.assertNotEqual(baseline['normalized_stokes'],wrong_analyzer['normalized_stokes'])

    def test_sidecar_exact_hash_settings_and_immutable_completed_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            log=RotationLog(folder,metadata={'experiment_id':'e'})
            rows=list(records(JonesModel()))
            for r in rows:log.write_sample(r)
            log.finish(phase='COMPLETED',detail='simulated fixture',metrics={'association_complete':True})
            before=log.manifest_path.read_bytes()
            dest,result=analyze_csv(log.sample_path,zero_offset_deg=0,analyzer_deg=0,calibration_id='optical-zero-test')
            self.assertTrue(dest.exists())
            self.assertEqual(log.manifest_path.read_bytes(),before)
            self.assertEqual(result['input_sha256'],hashlib.sha256(log.sample_path.read_bytes()).hexdigest())
            self.assertEqual(result['analysis']['calibration_id'],'optical-zero-test')
            with self.assertRaises(FileExistsError):analyze_csv(log.sample_path,zero_offset_deg=0,analyzer_deg=0,calibration_id='x',output=dest)
            content=log.sample_path.read_text();log.sample_path.write_text(content+'\n')
            with self.assertRaisesRegex(ValueError,'finalized acquisition hash'):
                analyze_csv(log.sample_path,zero_offset_deg=0,analyzer_deg=0,calibration_id='x')
            log.sample_path.write_text(content)
            bad=json.loads(before);bad['phase']='CANCELLED';log.manifest_path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError,'completed'):analyze_csv(log.sample_path,zero_offset_deg=0,analyzer_deg=0,calibration_id='x')
