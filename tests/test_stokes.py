"""Independent known-state Jones and analytic harmonic fixtures; simulated only."""
import cmath
import math
import random
import unittest

from polarimeter_core import JonesModel
from polarimeter_stokes import exposure_design,fit_records


def records(model, *, zero=0, noise=0, gaps=False):
    rng=random.Random(12)
    for i in range(720):
        if gaps and 180<i<240:continue
        lo,hi=i,i+.3
        # Independent numerical exposure integral from existing Jones model.
        n=20
        intensity=sum(model.intensity(lo+zero+(hi-lo)*(j+.5)/n) for j in range(n))/n
        yield dict(mode='SIMULATED',quality='VALID',experiment_id='e',boot_id='b',session_id='s',
            arm_generation='1',motion_id='5',reference_epoch='r',config_id='c',calibration_id='k',
            sequence=i+1,mapped_start_us=i*20000,mapped_end_us=i*20000+10000,
            estimated_start_deg=lo,estimated_end_deg=hi,intensity_au=intensity+rng.gauss(0,noise))


def truth(model):
    return [abs(model.ex)**2+abs(model.ey)**2,abs(model.ex)**2-abs(model.ey)**2,
            2*(model.ex*model.ey.conjugate()).real,2*(model.ex*model.ey.conjugate()).imag]


def fit(rows,zero=0,analyzer=0):
    return fit_records(rows,zero_offset_deg=zero,analyzer_deg=analyzer,calibration_id='explicit-test-zero')


class StokesTests(unittest.TestCase):
    def test_linear_circular_and_elliptical_with_nonzero_analyzer(self):
        for ex,ey in ((1,0),(0,1),(2**-.5,2**-.5),(2**-.5,1j*2**-.5),(.8,.6*cmath.exp(.7j))):
            model=JonesModel(ex=complex(ex),ey=complex(ey),analyzer_deg=17)
            result=fit(records(model),analyzer=17)
            for a,b in zip(result['stokes'],truth(model)):self.assertAlmostEqual(a,b,delta=1e-7)
            self.assertEqual(result['rank'],4)
            self.assertAlmostEqual(result['dop'],1,delta=1e-7)

    def test_exposure_average_and_sign_agree_with_jones_over_long_exposure(self):
        m=JonesModel(ex=2**-.5,ey=1j*2**-.5,analyzer_deg=23)
        row=exposure_design(12,52,zero_offset_deg=5,analyzer_deg=23)
        expected=sum(m.intensity(17+40*(j+.5)/10000) for j in range(10000))/10000
        self.assertAlmostEqual(float(row@truth(m)),expected,delta=1e-8)
        self.assertEqual(truth(m)[3],-1.0000000000000002)

    def test_oracle_calibrated_and_uncalibrated_offset_comparison(self):
        m=JonesModel(ex=.8,ey=.6j)
        rows=list(records(m,zero=5))
        oracle=[dict(r,estimated_start_deg=r['estimated_start_deg']+5,
                     estimated_end_deg=r['estimated_end_deg']+5) for r in rows]
        a,b,c=fit(oracle),fit(rows,zero=5),fit(rows)
        for x,y in zip(a['stokes'],b['stokes']):self.assertAlmostEqual(x,y)
        self.assertGreater(max(abs(x-y) for x,y in zip(c['stokes'],truth(m))),.02)
        # Poison all truth fields: the normal estimator must never read them.
        poisoned=[dict(r,optical_truth_deg=float('nan'),truth_start_deg=-999,truth_end_deg=-999) for r in rows]
        self.assertEqual(fit(poisoned,zero=5)['stokes'],b['stokes'])

    def test_noise_gaps_uncertainty_and_nonphysical_dop_not_clipped(self):
        m=JonesModel(ex=.8,ey=.6j)
        r=fit(records(m,noise=.001,gaps=True))
        self.assertGreater(r['valid_segment_gaps'],0)
        self.assertGreater(r['valid_midpoint_span_deg'],r['contiguous_midpoint_coverage_deg'])
        self.assertGreater(r['dop_standard_error'],0)
        self.assertAlmostEqual(r['dop'],1,delta=.005)
        rows=list(records(m))
        for row in rows:
            design=exposure_design(row['estimated_start_deg'],row['estimated_end_deg'],zero_offset_deg=0,analyzer_deg=0)
            row['intensity_au']=float(design@[1,0,0,1.2])
        r=fit(rows)
        self.assertGreater(r['dop'],1)
        self.assertIn('NONPHYSICAL_DOP',r['flags'])

    def test_rank_bad_time_identity_and_hardware_are_rejected(self):
        rows=list(records(JonesModel()))
        for row in rows:row['estimated_start_deg']=row['estimated_end_deg']=0
        with self.assertRaisesRegex(ValueError,'Rank'):fit(rows)
        for changes in ({'mode':'HARDWARE'},{'session_id':'other'},{'mapped_end_us':-1},{'intensity_au':float('nan')}):
            rows=list(records(JonesModel()))
            rows[10].update(changes)
            with self.assertRaises(ValueError):fit(rows)
        with self.assertRaises(ValueError):fit_records([],zero_offset_deg=0,analyzer_deg=0,calibration_id='')
