# Simulated Stokes analysis increment

2026-11-27. **SIMULATED analysis only; no physical optical calibration.**
Separate from acquisition, implemented in `polarimeter_stokes.py`. The normal
estimator never reads optical truth, commanded angle or known input Jones state.
It requires VALID inferred exposure endpoints, explicit fast-axis zero offset,
fixed analyzer angle and optical-calibration identity. Acquisition calibration
IDs and full boot/session/arm/motion/reference identity must remain consistent.

## Model and convention

For `JonesModel`, fast axis u=(cos θ,sin θ), slow v=(-sin θ,cos θ),
J=uuᵀ+i vvᵀ. Define S0=|Ex|²+|Ey|², S1=|Ex|²−|Ey|²,
S2=2 Re(Ex conj(Ey)), **S3=2 Im(Ex conj(Ey))**. No external circular-handedness
label is inferred from this algebraic convention. With analyzer angle α,
φ=θ−α, Q=S1 cos 2α+S2 sin 2α and U=−S1 sin 2α+S2 cos 2α:

```text
I(θ) = S0/2 + Q (1+cos 4φ)/4 + U sin 4φ/4 − S3 sin 2φ/2
θ = inferred_angle + explicit_fast_axis_zero_offset
```

This is derived from the repository's Jones operator, independently checked
against numerical integration of `JonesModel` for multiple input states and
analyzer angles. The rotating-waveplate harmonic method and need for fast-axis
phase knowledge are also described by
[Wilkinson et al., arXiv:2104.06504](https://arxiv.org/html/2104.06504).
Our sign convention is explicitly specified above rather than assumed identical
to a paper's instrument convention.

Each design row uses analytic exposure averages of the 2θ/4θ harmonics:
`average cos(nφ)=cos(nφmid) sinc(n Δφ/2)` (and similarly sin).
This is exact for uniform angular motion; accepted steady-plateau samples use a
constant-speed approximation between their inferred endpoints. Detector physics
still integrates the separate true trajectory with a trapezoidal approximation.
These numerical/model differences remain visible in residuals. Neither ideal
quarter-wave retardance nor analyzer/zero/gain calibration is established on
physical optics.

## Fit, uncertainty and integrity

Stream blocks of at most 128 rows through a five-column QR reduction. Retain
only the reduced matrix and scalar diagnostics, then use least squares/SVD for
four Stokes coefficients, rank and conditioning. Reject rank below four,
condition above 1e8, nonfinite inputs, invalid time ordering and mixed acquisition
identities. Rank/conditioning, total valid midpoint span, contiguous midpoint
coverage, gaps and flagged/valid counts accompany residual RMS. Sparse data can
have full rank; coverage diagnostics do not certify calibration or accuracy.

Report normalized S1/S0, S2/S0, S3/S0, DoP and propagated standard errors. The
covariance uses residual variance with n−4 degrees of freedom and an IID,
equal-variance intensity-noise model. It excludes optical-zero, timing,
retardance, gain and angle uncertainty. DoP>1+1e−8 receives NONPHYSICAL_DOP and
is **never clipped**. S0≤0 is flagged and normalized results are unavailable.
Zero residual/noise does not establish zero physical uncertainty.

## Explicit use

First create a simulated rotation CSV using the acquisition CLI or Rotation lab.
Then fit its optical CSV, for example with the deliberately known demo +5°
fixture calibration (not a physical measured calibration):

```bash
.venv/bin/python -B -m polarimeter_stokes --simulate \
  --optical-csv data/simulated-rotation/YOUR-OPTICAL-FILE.csv \
  --zero-offset-deg 5 --analyzer-deg 0 --calibration-id simulated-demo-zero-plus5
```

Results print as JSON; acquisition manifests remain reconstruction PENDING
until an analysis is explicitly run. Physical detector fitting is unsupported.

Known-state tests cover linear, circular and elliptical inputs, nonzero analyzer,
finite exposures, phase offset, noise/gaps, deficient angular coverage and DoP>1.
Oracle tests explicitly replace estimated angles with fixture truth outside the
normal estimator, compare correct +5° inferred calibration and the uncalibrated
case, and poison truth fields to verify they cannot enter the normal fit.
See the [Stage 4 review follow-up](offline-rotation-stage4-review.md) for executed
counts, end-to-end results and remaining limitations.
