"""SIMULATED ideal-QWP Stokes fitting; no detector or hardware calibration.

Convention: S3 = 2 Im(Ex conj(Ey)), matching JonesModel's slow-axis +i phase.
Only VALID inferred-angle records enter the normal estimator. Explicit fast-axis
zero and analyzer angle are mandatory. Truth fields are never read here.
"""
import math
import numpy as np

from polarimeter_controller import number, decimal


def exposure_design(start_deg, end_deg, *, zero_offset_deg, analyzer_deg):
    """Exact harmonic averages for uniform angular motion over one exposure.

    This is a documented constant-speed exposure approximation to the accepted
    plateau; it does not substitute a midpoint for a finite exposure.
    """
    for v in (start_deg,end_deg,zero_offset_deg,analyzer_deg):
        if abs(number(v))>1e9:
            raise ValueError('Angle exceeds numerical analysis bound of 1e9 degrees')
    lo = math.radians(number(start_deg)+number(zero_offset_deg)-number(analyzer_deg))
    hi = math.radians(number(end_deg)+zero_offset_deg-analyzer_deg)
    mid, half = (lo+hi)/2, (hi-lo)/2
    def average(n, trig):
        x = n*half
        return trig(n*mid)*(1 if abs(x)<1e-12 else math.sin(x)/x)
    c4,s4,s2 = average(4,math.cos),average(4,math.sin),average(2,math.sin)
    alpha = math.radians(2*analyzer_deg)
    c,s = math.cos(alpha),math.sin(alpha)
    row = np.array([.5, .25*((1+c4)*c-s4*s), .25*((1+c4)*s+s4*c), -.5*s2])
    if not np.isfinite(row).all():raise ValueError("Nonfinite design row")
    return row


def _fit_records(records, *, zero_offset_deg, analyzer_deg, calibration_id, period_us=None):
    """Bounded streaming QR least squares (blocks <=128 rows, five columns).

    Residual-based uncertainties assume independent equal-variance intensity
    noise and exact model/angles. Clock, zero, retardance and gain systematics
    require separate calibration; this is simulated analysis only.
    """
    number(zero_offset_deg); number(analyzer_deg)
    if period_us is not None and (number(period_us)<=0 or period_us>1e9):
        raise ValueError('Invalid declared detector cadence')
    if not isinstance(calibration_id,str) or not calibration_id.strip():
        raise ValueError('Explicit simulated optical calibration identity required')
    triangular = np.empty((0,5))
    block=[]
    accepted=flagged=0
    identity=None
    first=last=previous=None
    contiguous=0.0
    gaps=0
    temporal_gaps=0
    direction=None
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
        if not math.isfinite(start) or not math.isfinite(end) or not 0<=start<end<=2**64-1:
            raise ValueError('Invalid exposure span')
        mid=start+(end-start)/2
        raw_seq=row['sequence']
        seq=decimal(str(raw_seq),positive=True) if type(raw_seq) in (int,str) else None
        if seq is None:raise ValueError('Positive canonical exposure sequence required')
        angle=(lo+hi)/2
        step_direction=1 if hi>lo else -1 if hi<lo else 0
        if step_direction:
            if direction is not None and direction!=step_direction:raise ValueError('Mixed exposure directions')
            direction=step_direction
        if previous is not None:
            if mid<=previous[1] or seq<=previous[0] or start<previous[3]:
                raise ValueError('Nonmonotonic fit records')
            if direction is not None and direction*(angle-previous[2])<0:
                raise ValueError('Reversed angular progression')
            time_gap=period_us is not None and mid-previous[1]>1.5*period_us
            if time_gap:temporal_gaps+=1
            if seq==previous[0]+1 and not time_gap: contiguous+=abs(angle-previous[2])
            else:gaps+=1
        previous=seq,mid,angle,end
        if first is None:first=angle
        last=angle
        design=exposure_design(lo,hi,zero_offset_deg=zero_offset_deg,analyzer_deg=analyzer_deg)
        block.append([*design,intensity])
        accepted+=1
        if len(block)==128:reduce_rows()
    if block:reduce_rows()
    if accepted<5:raise ValueError('At least five valid exposures required')
    if not np.isfinite(triangular).all():raise ValueError('Nonfinite QR result')
    if abs(last-first)<90:raise ValueError('At least 90 degrees of valid midpoint span required')
    matrix,y=triangular[:,:4],triangular[:,4]
    stokes,_,rank,singular=np.linalg.lstsq(matrix,y,rcond=None)
    condition=float(singular[0]/singular[-1]) if singular[-1]>0 else None
    flags=[]
    if period_us is None:flags.append("CADENCE_UNSPECIFIED")
    if temporal_gaps:flags.append("TEMPORAL_GAPS_IN_VALID_EXPOSURES")
    if gaps:flags.append("GAPS_IN_VALID_EXPOSURES")
    if flagged:flags.append("FLAGGED_EXPOSURES_EXCLUDED")
    if rank<4 or condition is None or condition>1e8:
        raise ValueError('Rank-deficient or ill-conditioned angular coverage')
    residual=matrix@stokes-y
    sse=max(0.0,float(residual@residual))
    variance=sse/(accepted-4)
    _,sv,vt=np.linalg.svd(matrix,full_matrices=False)
    covariance=variance*(vt.T@np.diag(1/sv**2)@vt)
    normalized=normalized_se=dop=dop_se=cov_normal=None
    if stokes[0]<=0:flags.append('NONPOSITIVE_S0')
    else:
        normalized=stokes[1:]/stokes[0]
        jac=np.column_stack((-normalized/stokes[0],np.eye(3)/stokes[0]))
        cov_normal=jac@covariance@jac.T
        normalized_se=np.sqrt(np.maximum(0,np.diag(cov_normal))).tolist()
        dop=float(np.linalg.norm(normalized))
        near_zero=max(1e-10,3*math.sqrt(max(0,float(np.trace(cov_normal)))))
        if dop<=near_zero:
            flags.append('DOP_NEAR_ZERO_DELTA_INVALID')
        elif dop>0:
            gradient=normalized/dop
            dop_se=math.sqrt(max(0,float(gradient@cov_normal@gradient)))
        if dop>1+1e-8:flags.append('NONPHYSICAL_DOP')
        normalized=normalized.tolist()
    result_arrays=[stokes,covariance]
    if normalized is not None:result_arrays.extend([normalized,cov_normal])
    if not all(np.isfinite(x).all() for x in result_arrays) or not all(math.isfinite(x) for x in (sse,variance)):
        raise ValueError('Nonfinite analysis result')
    return dict(mode='SIMULATED',analysis='ideal-qwp-exposure-averaged-stokes-v1',
        angle_source='ENCODER_INFERRED_WITH_EXPLICIT_ZERO', calibration_id=calibration_id,
        zero_offset_deg=zero_offset_deg,analyzer_deg=analyzer_deg,
        convention='S3=2 Im(Ex conj(Ey)); Jones slow-axis phase +pi/2',
        acquisition_identity=identity,valid_samples=accepted,flagged_samples=flagged,
        rank=int(rank),condition=condition,valid_midpoint_span_deg=abs(last-first),
        contiguous_midpoint_coverage_deg=contiguous,valid_segment_gaps=gaps,
        temporal_gap_count=temporal_gaps,declared_period_us=period_us,
        stokes=stokes.tolist(),stokes_units='uncalibrated incident-intensity-equivalent a.u. in ideal gain model',
        stokes_covariance=covariance.tolist(),
        normalized_covariance=None if cov_normal is None else cov_normal.tolist(),
        direction=direction,normalized_stokes=normalized,dop=dop,
        residual_rms=math.sqrt(sse/accepted),stokes_standard_error=np.sqrt(np.maximum(0,np.diag(covariance))).tolist(),
        normalized_standard_error=normalized_se,dop_standard_error=dop_se,flags=flags,
        uncertainty_model='IID intensity noise only; excludes angle/clock/calibration/model systematics',
        measured_hardware=False)


def fit_records(records, *, zero_offset_deg, analyzer_deg, calibration_id, period_us=None):
    try:
        with np.errstate(over='raise',invalid='raise',divide='raise'):
            result=_fit_records(records,zero_offset_deg=zero_offset_deg,analyzer_deg=analyzer_deg,
                                calibration_id=calibration_id,period_us=period_us)
            def finite_tree(value):
                if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite scalar analysis output')
                if isinstance(value,dict):
                    for child in value.values():finite_tree(child)
                elif isinstance(value,(list,tuple)):
                    for child in value:finite_tree(child)
            finite_tree(result)
            return result
    except (FloatingPointError,OverflowError,np.linalg.LinAlgError) as error:
        raise ValueError('Numerically invalid Stokes fit: '+str(error)) from error


def analyze_csv(path, *, zero_offset_deg, analyzer_deg, calibration_id, output=None):
    """Analyze a completed simulated acquisition and write a separate sidecar.

    Acquisition manifest is immutable. Hash before/after fitting prevents a
    changing input from being presented as the selected completed acquisition.
    """
    import csv
    import hashlib
    import json
    import os
    from pathlib import Path
    from uuid import uuid4
    from datetime import datetime,timezone
    path=Path(path)
    if not path.name.endswith('-optical.csv'):
        raise ValueError('Select a completed simulated rotation optical CSV')
    manifest_path=path.with_name(path.name[:-len('-optical.csv')]+'-session.json')
    manifest_bytes=manifest_path.read_bytes()
    manifest=json.loads(manifest_bytes)
    if not isinstance(manifest,dict) or any(not isinstance(manifest.get(k,{}),dict) for k in ('metadata','metrics','files','data_sha256')):
        raise ValueError('Malformed acquisition manifest structure')
    if (manifest.get('mode')!='SIMULATED' or manifest.get('measured_hardware') is not False
            or manifest.get('phase')!='COMPLETED' or manifest.get('data_complete') is not True):
        raise ValueError('Motion must be completed and acquisition data successfully finalized')
    if manifest.get('files',{}).get('optical',path.name)!=path.name:
        raise ValueError('Manifest optical filename mismatch')
    def digest():
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(65536),b''):h.update(chunk)
        return h.hexdigest()
    before=digest()
    recorded=manifest.get('data_sha256',{}).get('optical')
    if recorded is not None and before!=recorded:
        raise ValueError('Selected optical CSV differs from finalized acquisition hash')
    identity_keys=('experiment_id','boot_id','session_id','arm_generation','motion_id','reference_epoch','config_id','calibration_id')
    observed=set()
    def checked_rows(stream):
        for row in csv.DictReader(stream):
            values=tuple(row.get(k) for k in identity_keys)
            if any(v is None or v=='' for v in values):raise ValueError('Incomplete optical acquisition identity')
            observed.add(values)
            if len(observed)>8:raise ValueError('Too many acquisition identities')
            yield row
    with path.open(newline='',encoding='utf-8') as f:
        result=fit_records(checked_rows(f),zero_offset_deg=zero_offset_deg,analyzer_deg=analyzer_deg,
            calibration_id=calibration_id,period_us=manifest.get('metadata',{}).get('period_us'))
    for values in observed:
        if values[0]!=manifest.get('metadata',{}).get('experiment_id'):
            raise ValueError('Manifest/optical experiment identity mismatch')
    recorded_identities=manifest.get('acquisition_identities')
    if recorded_identities is not None:
        if not isinstance(recorded_identities,list) or len(recorded_identities)>8 or any(not isinstance(item,dict) for item in recorded_identities):
            raise ValueError('Malformed manifest acquisition identities')
        if any(type(item.get(k)) is not str or not item.get(k) for item in recorded_identities for k in identity_keys):
            raise ValueError('Manifest acquisition identity values must be nonempty strings')
        recorded_set={tuple(item.get(k) for k in identity_keys) for item in recorded_identities}
        if len(recorded_set)!=len(recorded_identities) or recorded_set!=observed:raise ValueError('Manifest/full optical acquisition identity mismatch')
    metadata_config=manifest.get('metadata',{}).get('config',{})
    if not isinstance(metadata_config,dict):raise ValueError('Manifest acquisition configuration must be an object')
    for index,key in ((6,'config_id'),(7,'calibration_id')):
        if key in metadata_config and any(values[index]!=str(metadata_config[key]) for values in observed):
            raise ValueError('Manifest acquisition configuration mismatch')
    total=result['valid_samples']+result['flagged_samples']
    for actual,key in ((total,'written_exposure_rows'),):
        if key in manifest and (type(manifest[key]) is not int or manifest[key]!=actual):raise ValueError('Manifest exposure row count mismatch')
    metrics=manifest.get('metrics',{})
    for key,actual in (('valid_samples',result['valid_samples']),('rejected_samples',result['flagged_samples']),('received_exposures',total)):
        if key in metrics and (type(metrics[key]) is not int or metrics[key]!=actual):
            raise ValueError('Manifest acquisition count mismatch: '+key)
    if all(k in metrics for k in ('expected_exposures','missing_exposures','pending_exposures')):
        if any(type(metrics[k]) is not int or metrics[k]<0 for k in ('expected_exposures','missing_exposures','pending_exposures')):
            raise ValueError('Invalid manifest detector counts')
        if metrics['expected_exposures']!=total+metrics['missing_exposures']+metrics['pending_exposures']:
            raise ValueError('Manifest detector accounting mismatch')
    if before!=digest() or manifest_bytes!=manifest_path.read_bytes():
        raise ValueError('Acquisition input changed during analysis')
    if result['acquisition_identity'][0]!=manifest.get('metadata',{}).get('experiment_id'):
        raise ValueError('Manifest/optical experiment identity mismatch')
    if manifest.get('metrics',{}).get('missing_exposures',0):result['flags'].append('DETECTOR_EXPOSURES_MISSING')
    destination=Path(output) if output else path.with_name(path.stem+'-analysis-'+uuid4().hex[:12]+'.json')
    if destination.exists():raise FileExistsError(destination)
    if destination.resolve() in (path.resolve(),manifest_path.resolve()):raise ValueError('Analysis cannot overwrite acquisition')
    record=dict(mode='SIMULATED',measured_hardware=False,created_utc=datetime.now(timezone.utc).isoformat(),
        input_file=path.name,input_sha256=before,input_bytes=path.stat().st_size,
        acquisition_manifest=manifest_path.name,manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        motion_phase=manifest['phase'],acquisition_metrics=manifest.get('metrics',{}),
        input_integrity='FINALIZED_HASH_MATCH' if recorded else 'LEGACY_MANIFEST_WITHOUT_HASH',
        observed_acquisition_identities=[dict(zip(identity_keys,v)) for v in sorted(observed)],analysis=result)
    # Create exclusive destination via a hard link to a complete temporary file;
    # never replace an existing result, CSV or immutable acquisition manifest.
    temporary=destination.with_name(destination.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(record,f,indent=2,allow_nan=False);f.flush()
        os.link(temporary,destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination,record

def main():
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulate',action='store_true')
    parser.add_argument('--optical-csv',required=True)
    parser.add_argument('--zero-offset-deg',type=float,required=True)
    parser.add_argument('--analyzer-deg',type=float,required=True)
    parser.add_argument('--calibration-id',required=True)
    parser.add_argument('--analysis-out',help='New JSON sidecar path; existing files are never overwritten')
    args=parser.parse_args()
    if not args.simulate:parser.error('Explicit --simulate is required')
    try:
        destination,result=analyze_csv(args.optical_csv,zero_offset_deg=args.zero_offset_deg,
            analyzer_deg=args.analyzer_deg,calibration_id=args.calibration_id,output=args.analysis_out)
    except (OSError,ValueError,ArithmeticError,np.linalg.LinAlgError) as error:
        parser.error(str(error))
    print(json.dumps(dict(sidecar=str(destination),**result),indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
