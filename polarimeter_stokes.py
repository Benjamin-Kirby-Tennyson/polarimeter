"""SIMULATED ideal-QWP Stokes fitting; no detector or hardware calibration.

Convention: S3 = 2 Im(Ex conj(Ey)), matching JonesModel's slow-axis +i phase.
Only VALID inferred-angle records enter the normal estimator. Explicit fast-axis
zero and analyzer angle are mandatory. Truth fields are never read here.
"""
import math
import numpy as np

from polarimeter_controller import number


def exposure_design(start_deg, end_deg, *, zero_offset_deg, analyzer_deg):
    """Exact harmonic averages for uniform angular motion over one exposure.

    This is a documented constant-speed exposure approximation to the accepted
    plateau; it does not substitute a midpoint for a finite exposure.
    """
    lo = math.radians(number(start_deg)+number(zero_offset_deg)-number(analyzer_deg))
    hi = math.radians(number(end_deg)+zero_offset_deg-analyzer_deg)
    mid, half = (lo+hi)/2, (hi-lo)/2
    def average(n, trig):
        x = n*half
        return trig(n*mid)*(1 if abs(x)<1e-12 else math.sin(x)/x)
    c4,s4,s2 = average(4,math.cos),average(4,math.sin),average(2,math.sin)
    alpha = math.radians(2*analyzer_deg)
    c,s = math.cos(alpha),math.sin(alpha)
    return np.array([.5, .25*((1+c4)*c-s4*s), .25*((1+c4)*s+s4*c), -.5*s2])


def fit_records(records, *, zero_offset_deg, analyzer_deg, calibration_id):
    """Bounded streaming QR least squares (blocks <=128 rows, five columns).

    Residual-based uncertainties assume independent equal-variance intensity
    noise and exact model/angles. Clock, zero, retardance and gain systematics
    require separate calibration; this is simulated analysis only.
    """
    number(zero_offset_deg); number(analyzer_deg)
    if not isinstance(calibration_id,str) or not calibration_id.strip():
        raise ValueError('Explicit simulated optical calibration identity required')
    triangular = np.empty((0,5))
    block=[]
    accepted=flagged=0
    identity=None
    first=last=previous=None
    contiguous=0.0
    gaps=0
    def reduce_rows():
        nonlocal triangular,block
        rows=np.vstack((triangular,np.asarray(block,dtype=float)))
        triangular=np.linalg.qr(rows,mode='r')
        block=[]
    for row in records:
        if row.get('mode')!='SIMULATED':
            raise ValueError('Only explicitly SIMULATED exposure records supported')
        if row.get('quality')!='VALID':
            flagged+=1
            continue
        provenance=tuple(row.get(k) for k in ('experiment_id','boot_id','session_id','arm_generation',
                                             'motion_id','reference_epoch','config_id','calibration_id'))
        if any(v is None or v=='' for v in provenance):
            raise ValueError('Missing immutable acquisition identity')
        if identity is not None and provenance!=identity:
            raise ValueError('Mixed acquisition identities require separate fits')
        identity=provenance
        lo,hi=float(row['estimated_start_deg']),float(row['estimated_end_deg'])
        intensity=float(row['intensity_au'])
        if not all(math.isfinite(v) for v in (lo,hi,intensity)):
            raise ValueError('Nonfinite fit record')
        start,end=float(row['mapped_start_us']),float(row['mapped_end_us'])
        if not math.isfinite(start) or not math.isfinite(end) or end<=start:
            raise ValueError('Invalid exposure span')
        mid=(start+end)/2
        seq=int(row['sequence'])
        angle=(lo+hi)/2
        if previous is not None:
            if mid<=previous[1] or seq<=previous[0]:
                raise ValueError('Nonmonotonic fit records')
            if seq==previous[0]+1: contiguous+=abs(angle-previous[2])
            else:gaps+=1
        previous=seq,mid,angle
        if first is None:first=angle
        last=angle
        design=exposure_design(lo,hi,zero_offset_deg=zero_offset_deg,analyzer_deg=analyzer_deg)
        block.append([*design,intensity])
        accepted+=1
        if len(block)==128:reduce_rows()
    if block:reduce_rows()
    if accepted<5:raise ValueError('At least five valid exposures required')
    matrix,y=triangular[:,:4],triangular[:,4]
    stokes,_,rank,singular=np.linalg.lstsq(matrix,y,rcond=None)
    condition=float(singular[0]/singular[-1]) if singular[-1]>0 else None
    flags=[]
    if rank<4 or condition is None or condition>1e8:
        raise ValueError('Rank-deficient or ill-conditioned angular coverage')
    residual=matrix@stokes-y
    sse=max(0.0,float(residual@residual))
    variance=sse/(accepted-4)
    _,sv,vt=np.linalg.svd(matrix,full_matrices=False)
    covariance=variance*(vt.T@np.diag(1/sv**2)@vt)
    normalized=normalized_se=dop=dop_se=None
    if stokes[0]<=0:flags.append('NONPOSITIVE_S0')
    else:
        normalized=stokes[1:]/stokes[0]
        jac=np.column_stack((-normalized/stokes[0],np.eye(3)/stokes[0]))
        cov_normal=jac@covariance@jac.T
        normalized_se=np.sqrt(np.maximum(0,np.diag(cov_normal))).tolist()
        dop=float(np.linalg.norm(normalized))
        if dop>0:
            gradient=normalized/dop
            dop_se=math.sqrt(max(0,float(gradient@cov_normal@gradient)))
        if dop>1+1e-8:flags.append('NONPHYSICAL_DOP')
        normalized=normalized.tolist()
    return dict(mode='SIMULATED',analysis='ideal-qwp-exposure-averaged-stokes-v1',
        angle_source='ENCODER_INFERRED_WITH_EXPLICIT_ZERO', calibration_id=calibration_id,
        zero_offset_deg=zero_offset_deg,analyzer_deg=analyzer_deg,
        convention='S3=2 Im(Ex conj(Ey)); Jones slow-axis phase +pi/2',
        acquisition_identity=identity,valid_samples=accepted,flagged_samples=flagged,
        rank=int(rank),condition=condition,valid_midpoint_span_deg=abs(last-first),
        contiguous_midpoint_coverage_deg=contiguous,valid_segment_gaps=gaps,
        stokes=stokes.tolist(),normalized_stokes=normalized,dop=dop,
        residual_rms=math.sqrt(sse/accepted),stokes_standard_error=np.sqrt(np.maximum(0,np.diag(covariance))).tolist(),
        normalized_standard_error=normalized_se,dop_standard_error=dop_se,flags=flags,
        uncertainty_model='IID intensity noise only; excludes angle/clock/calibration/model systematics',
        measured_hardware=False)


def main():
    import argparse
    import csv
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulate',action='store_true')
    parser.add_argument('--optical-csv',required=True)
    parser.add_argument('--zero-offset-deg',type=float,required=True)
    parser.add_argument('--analyzer-deg',type=float,required=True)
    parser.add_argument('--calibration-id',required=True)
    args=parser.parse_args()
    if not args.simulate:parser.error('Explicit --simulate is required')
    with open(args.optical_csv,newline='',encoding='utf-8') as stream:
        result=fit_records(csv.DictReader(stream),zero_offset_deg=args.zero_offset_deg,
                           analyzer_deg=args.analyzer_deg,calibration_id=args.calibration_id)
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
