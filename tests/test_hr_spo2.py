"""HrSpO2Estimator on synthetic red/IR signals with a known heart rate and
ratio of ratios. These pin the behaviour the examples rely on; README.md
gives the estimator's accuracy on real recordings.
"""
import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from max30102.hr_spo2 import (  # noqa: E402
    MIN_AUTOCORRELATION_RATIO, Estimate, HrSpO2Estimator)

FS = 25
# The band-pass rings for a few seconds after the first sample. With the
# default 6 s window and 1 s updates, estimate i covers seconds [i, i + 6), so
# skipping the first STARTUP_S estimates skips every window that contains it.
STARTUP_S = 4


def ppg(bpm_at, seconds, r=0.5, noise=0.0, seed=0, start_s=0.0):
    """Red/IR counts with a pulse whose rate follows bpm_at(t). Counts drop
    at systole, as on the real sensor; IR perfusion index 0.5 %."""
    rng = np.random.default_rng(seed)
    t = start_s + np.arange(int(seconds * FS)) / FS
    phase = 2 * math.pi * np.cumsum([bpm_at(x) / 60 / FS for x in t])
    pulse = np.sin(phase) + 0.4 * np.sin(2 * phase + 1.0)  # with a dicrotic bump
    pulse /= np.ptp(pulse)
    ir = 100_000 * (1 - 0.005 * pulse) + noise * rng.standard_normal(len(t))
    red = 80_000 * (1 - 0.005 * r * pulse) + noise * rng.standard_normal(len(t))
    return np.round(red).astype(int), np.round(ir).astype(int)


def run(estimator, red, ir):
    out = []
    for r_, i_ in zip(red, ir):
        e = estimator.add_sample(int(r_), int(i_))
        if e is not None:
            out.append(e)
    return out


def settled(estimates):
    return estimates[STARTUP_S:]


def spo2_curve(r):
    return (-45.060 * r + 30.354) * r + 94.845


def test_first_estimate_after_one_window_then_one_per_second():
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: 72, 20)
    ends = [k + 1 for k in range(len(ir)) if est.add_sample(int(red[k]), int(ir[k])) is not None]
    assert ends[0] == 6 * FS
    assert all(b - a == FS for a, b in zip(ends, ends[1:]))
    assert len(ends) == 15
    assert all(isinstance(e, Estimate) for e in run(HrSpO2Estimator(), red, ir))


@pytest.mark.parametrize("bpm", [50, 72, 110, 150, 165, 175])
def test_heart_rate_and_spo2_of_a_clean_pulse(bpm):
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: bpm, 24, r=0.6, noise=20)
    for e in settled(run(est, red, ir)):
        assert abs(e.heart_rate - bpm) <= max(5, 0.1 * bpm)
        assert abs(e.r - 0.6) < 0.05
        assert abs(e.spo2 - spo2_curve(e.r)) < 1e-9
        assert e.correlation > 0.8
        assert not e.below_range


def test_heart_rate_without_spo2_when_red_is_noisy():
    # The heart rate comes from IR alone; SpO2 needs red and IR to agree
    # closely, because the ratio of ratios assumes both see the same pulse
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: 72, 24, noise=20)
    red = red + np.round(np.random.default_rng(2).normal(0, 200, len(red))).astype(int)
    for e in settled(run(est, red, ir)):
        assert abs(e.heart_rate - 72) <= 7.2
        assert 0 < e.correlation < 0.8
        assert e.spo2 is None
        assert e.r is not None


def test_no_reading_without_a_pulse():
    est = HrSpO2Estimator()
    rng = np.random.default_rng(1)
    ir = 100_000 + rng.normal(0, 50, 20 * FS)
    red = 80_000 + rng.normal(0, 50, 20 * FS)
    for e in run(est, red, ir):
        assert e.heart_rate is None and e.spo2 is None and not e.below_range


def test_no_reading_when_a_channel_is_flat():
    # e.g. the ADC is saturated, or nothing reflects any light
    est = HrSpO2Estimator()
    out = run(est, [262_143] * 200, [262_143] * 200)
    assert out == [Estimate(None, None, None, None, False)] * 3


def test_follows_an_abrupt_rise_in_heart_rate():
    # Plain RF would lock onto the second autocorrelation peak here and keep
    # reporting about half the new rate. Estimate i covers seconds [i, i + 6):
    # windows 4-14 lie before the step at 20 s, windows from 20 on after it.
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: 65 if t < 20 else 125, 40, noise=20)
    out = run(est, red, ir)
    assert all(abs(e.heart_rate - 65) <= 6.5 for e in out[STARTUP_S:15])
    assert all(abs(e.heart_rate - 125) <= 12.5 for e in out[20:])


def test_no_half_rate_reading_at_the_top_of_the_range():
    # At 180 BPM the period (8.3 samples) is right at the shortest lag the
    # search accepts; plain RF would report ~90 BPM here
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: 180, 24, noise=20)
    for e in settled(run(est, red, ir)):
        assert e.heart_rate is None or abs(e.heart_rate - 180) <= 18


@pytest.mark.parametrize("bpm", [190, 205, 220])
def test_reads_pulses_faster_than_rf_range(bpm):
    # RF alone would report about half these rates
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: bpm, 24, noise=20)
    for e in settled(run(est, red, ir)):
        assert abs(e.heart_rate - bpm) <= 0.1 * bpm


@pytest.mark.parametrize("bpm", [33, 35, 38])
def test_flags_a_pulse_below_the_range(bpm):
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: bpm, 24, noise=20)
    for e in settled(run(est, red, ir)):
        assert e.heart_rate is None and e.spo2 is None
        assert e.below_range


@pytest.mark.parametrize("bpm", [20, 25])
def test_no_flag_below_the_band_pass(bpm):
    # Below 30 BPM the pulse is in the band the filter removes (and where
    # breathing is), so it can't be told from anything else
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: bpm, 24, noise=20)
    for e in run(est, red, ir):
        assert e.heart_rate is None and not e.below_range


def test_double_period_of_a_normal_pulse_is_not_below_range():
    # A pulse also repeats every second beat, and that repeat passes RF's
    # autocorrelation test inside the below-range search. There is no energy
    # at half the pulse rate, though, so it is not taken for a slow pulse.
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: 75, 10, noise=20)
    run(est, red, ir)
    w = est._window()
    two_beats = 2 * FS * 60 // 75  # 40 samples
    acf = [est._acf(w.x, k) for k in (two_beats - 1, two_beats, two_beats + 1)]
    assert acf[0] < acf[1] >= acf[2]
    assert acf[1] / w.ir_power >= MIN_AUTOCORRELATION_RATIO
    assert not est._slower_than_range(w)


def test_reset_discards_the_partial_window():
    est = HrSpO2Estimator()
    red, ir = ppg(lambda t: 72, 10)
    assert run(est, red[:60], ir[:60]) == []
    est.reset()
    # Without the reset, these 90 samples would complete a 150-sample window
    assert run(est, red[60:150], ir[60:150]) == []
    assert len(run(est, red[150:210], ir[150:210])) == 1


def test_rejects_rates_too_low_for_the_band_pass():
    with pytest.raises(ValueError):
        HrSpO2Estimator(sample_rate=8)


def test_rejects_a_window_shorter_than_the_slowest_period():
    # The below-range check looks for periods down to 30 BPM (2 s)
    with pytest.raises(ValueError):
        HrSpO2Estimator(window_s=2)
