"""Heart rate and SpO2 from MAX30102 red/IR samples, for a seated user.

The estimator is Robert Fraczkiewicz's autocorrelation algorithm
(MAX30102_by_RF, algorithm_by_RF.cpp; see THIRD_PARTY_LICENSES.md). It
measures the period at which the IR waveform repeats, instead of picking
individual peaks. Real fingertip pulses often have a dicrotic notch, a second
bump ~0.3 s after each beat, which peak pickers count as a beat of its own;
the repeat period doesn't care how many bumps a beat has.

It is meant for someone sitting still: the choices below favour a steady
heart rate over following fast changes or riding out movement. While walking
or running it is right far less often, and walking can raise below_range.

What is built around RF's period search, and why:

- A causal 0.5-4 Hz band-pass (30-240 BPM) in front of it. RF only removes a
  straight line from each window, so breathing and slow finger-pressure
  changes would otherwise dominate the autocorrelation. The search sees the
  filtered band only. SpO2 takes each channel's DC level from the raw samples
  of the same window, because the ratio of ratios divides each channel's
  pulse amplitude by its level over the same interval.
- 6 s windows, one estimate per second. A seated heart rate is steady, so a
  longer window averages more beats into each period estimate, and noise
  rarely passes the repeat check over 6 s. The cost: the first estimate
  needs a full window, and one movement spoils every window that contains it.
- The period to a fraction of a sample, from a parabola through the
  autocorrelation peak. At 25 samples/s whole-sample lags are coarse: lags
  14, 15 and 16 are 107, 100 and 94 BPM.
- Each output has the quality check it depends on:
  - Heart rate comes from IR alone. The IR must repeat (autocorrelation peak
    >= 0.5 x its value at lag 0, RF's threshold), and red and IR must move
    together at all (correlation > 0; a pulse changes both the same way).
  - SpO2 also needs red and IR to agree closely (correlation >= 0.8, RF's
    threshold), because the ratio of ratios assumes both channels see the
    same pulse. The red channel's pulse is weaker relative to its noise than
    the IR's, so a heart rate often comes without SpO2.
- Checks where RF's search would report half the heart rate: after a fast
  rise in heart rate, and above ~155 BPM (see _find_period() and
  _initial_period()). Above RF's 180 BPM limit it also searches the shorter
  periods, up to 220 BPM (see _faster_than_range()).

Range. Heart rates read from ~42 to 220 BPM. The period search stops at
40 BPM (RF's limit) for a reason: a pulse also repeats every second beat, and
when its single-beat repeat is weak the autocorrelation can't tell it from a
pulse at half the rate (75 BPM would read as 37).

Below that, the estimator gives no heart rate but sets Estimate.below_range
when the IR repeats at a period between 40 BPM and the filter's 30 BPM edge,
and that period is the signal's own rather than a multiple of a shorter one
(see _slower_than_range()). That most likely means a pulse below 40 BPM, but:

- a 30-40 per minute rhythm from breathing or finger pressure, on a finger
  whose pulse is weak, looks the same: it changes red and IR together, like
  a pulse does;
- a pulse below 30 BPM is inside the breathing band, so it gives neither a
  heart rate nor the flag.

So the flag is a prompt to check, not a measurement; examples/heart_rate_spo2.py
shows one way to use it.

Show "no reading" when heart_rate is None. The quality checks are where the
accuracy comes from; holding the last value on screen would throw that away.

SpO2 uses the generic Maxim calibration curve. It is not calibrated for any
particular sensor, board or LED current; absolute values can be off by a few
percentage points. Not for medical use.

Needs numpy and scipy (pip install max30102[hrspo2]).
"""
import math
from collections import namedtuple

import numpy as np
from scipy.signal import butter, sosfilt

# RF's limits and quality thresholds (algorithm_by_RF.h)
MIN_HR = 40
MAX_HR = 180
MIN_AUTOCORRELATION_RATIO = 0.5
# RF's red/IR correlation threshold, applied to SpO2 only
MIN_SPO2_CORRELATION = 0.8

BANDPASS_HZ = (0.5, 4.0)

# The fastest pulse looked for above MAX_HR (see _faster_than_range())
FASTEST_HR = 220

Estimate = namedtuple('Estimate', 'heart_rate spo2 r correlation below_range', defaults=(False,))
Estimate.__doc__ = """One window's result.

heart_rate   BPM (int), or None when the window didn't pass the quality
             checks. Above MAX_HR (180) it comes from the extended search,
             which has only been tested on simulated pulses.
spo2         % (float), or None: no heart rate, red and IR don't agree
             closely enough (correlation < 0.8), or r outside the curve's
             range
r            ratio of ratios, (red AC/DC) / (IR AC/DC), with every heart
             rate; None without one
correlation  red/IR Pearson correlation of the window, or None if a channel
             was flat (no light, or the ADC saturated)
below_range  True when there is no heart rate but the IR repeats 30-40 times
             a minute: most likely a pulse below 40 BPM, though breathing or
             pressure changes on a weak pulse look the same
"""

_Window = namedtuple('_Window', 'x ir_power red_power ir_mean red_mean correlation')


class _BandPass:
    """Causal band-pass of one channel. The filter state carries over between
    calls, so filtering a stream piece by piece gives the same result as
    filtering it in one go."""

    def __init__(self, sample_rate):
        self._sos = butter(2, BANDPASS_HZ, btype='bandpass', fs=sample_rate, output='sos')
        self.reset()

    def reset(self):
        self._zi = np.zeros((self._sos.shape[0], 2))
        self._first = None

    def __call__(self, x):
        x = np.asarray(x, dtype=float)
        if self._first is None:
            # The filter starts at rest. Starting it on the raw counts would
            # make it ring with the full DC level (~10^5 counts) for seconds;
            # relative to the first sample, the start-up step is only as big
            # as the pulse.
            self._first = x[0]
        y, self._zi = sosfilt(self._sos, x - self._first, zi=self._zi)
        return y


class HrSpO2Estimator:
    """Heart rate and SpO2 from the last window_s seconds, every update_s.

    Feed it every (red, IR) pair in order, both from the same FIFO sample, at
    25 samples/s: e.g. setup_sensor(sample_rate=100, sample_avg=4,
    pulse_width=411). RF's period search and thresholds were tuned and
    validated at 25 samples/s; other rates work but are untested.

    The first estimate comes once window_s seconds of samples have arrived,
    then one every update_s seconds. Consecutive estimates share most of
    their samples, so they are not independent of each other.

    Call reset() when the sample stream has a gap (the driver's lost_samples
    or power_resets went up, or you stopped reading for a while): a window
    spliced across a gap has the wrong period.
    """

    def __init__(self, sample_rate=25, window_s=6, update_s=1):
        if sample_rate <= 2 * BANDPASS_HZ[1]:
            raise ValueError('sample_rate must be above {0} samples/s'.format(2 * BANDPASS_HZ[1]))
        self.sample_rate = sample_rate
        self.window_s = window_s
        self.update_s = update_s
        self._n = int(round(sample_rate * window_s))
        self._hop = max(1, int(round(sample_rate * update_s)))
        self._fs60 = sample_rate * 60
        self._lowest_period = int(self._fs60 / MAX_HR)
        self._highest_period = int(self._fs60 / MIN_HR)
        self._shortest_lag = int(self._fs60 / FASTEST_HR)
        # Longest lag the below-range check looks at: one period of the
        # band-pass's lower edge (30 BPM), plus the lag after it
        self._slowest_period = int(sample_rate / BANDPASS_HZ[0])
        if self._slowest_period + 1 >= self._n:
            raise ValueError('window_s is too short for {0} BPM'.format(int(60 * BANDPASS_HZ[0])))
        self._red_filter = _BandPass(sample_rate)
        self._ir_filter = _BandPass(sample_rate)
        self.reset()

    def reset(self):
        """Forget the buffered samples, the filter state and the last period."""
        self._new_red = []
        self._new_ir = []
        self._raw_red = self._raw_ir = self._ac_red = self._ac_ir = np.empty(0)
        self._red_filter.reset()
        self._ir_filter.reset()
        self._last_lag = self._lowest_period

    def add_sample(self, red, ir):
        """Add one (red, IR) pair. Returns an Estimate every update_s seconds
        of samples once a full window has arrived, otherwise None."""
        self._new_red.append(red)
        self._new_ir.append(ir)
        if len(self._new_ir) < self._hop:
            return None
        red = np.asarray(self._new_red, dtype=float)
        ir = np.asarray(self._new_ir, dtype=float)
        self._new_red = []
        self._new_ir = []
        n = self._n
        self._raw_red = np.concatenate((self._raw_red, red))[-n:]
        self._raw_ir = np.concatenate((self._raw_ir, ir))[-n:]
        self._ac_red = np.concatenate((self._ac_red, self._red_filter(red)))[-n:]
        self._ac_ir = np.concatenate((self._ac_ir, self._ir_filter(ir)))[-n:]
        if len(self._raw_ir) < n:
            return None
        return self._estimate()

    def _estimate(self):
        """The estimate for the current window. Updates the period the next
        window's search starts from."""
        w = self._window()
        if w is None:
            self._last_lag = self._lowest_period
            return Estimate(None, None, None, None)

        lag = self._find_period(w.x, w.ir_power) if w.correlation > 0 else 0
        # Without a period, start the next search from scratch: the last
        # period says nothing about a window that wasn't periodic
        self._last_lag = lag or self._lowest_period

        fast = self._faster_than_range(w, lag)
        if fast:
            # A period outside RF's range can't seed its tracking search
            self._last_lag = self._lowest_period
            return self._reading(w, fast)
        if lag:
            return self._reading(w, self._peak_position(w.x, lag))
        return Estimate(None, None, None, w.correlation,
                        w.correlation > 0 and self._slower_than_range(w))

    def _window(self):
        """Detrended band-passed IR and red with the statistics RF uses, and
        the raw window means as DC levels; None if a channel is flat."""
        x = self._detrend(self._ac_ir - self._ac_ir.mean())
        y = self._detrend(self._ac_red - self._ac_red.mean())
        n = len(x)
        ir_power = float(np.dot(x, x)) / n  # also the autocorrelation at lag 0
        red_power = float(np.dot(y, y)) / n
        if ir_power == 0 or red_power == 0:
            return None
        correlation = float(np.dot(x, y)) / n / math.sqrt(ir_power * red_power)
        return _Window(x, ir_power, red_power, float(self._raw_ir.mean()),
                       float(self._raw_red.mean()), correlation)

    def _reading(self, w, period):
        """The Estimate for a window with a heart rate; `period` in samples."""
        heart_rate = int(round(self._fs60 / period))
        if w.red_mean <= 0 or w.ir_mean <= 0:
            return Estimate(heart_rate, None, None, w.correlation)
        r = (math.sqrt(w.red_power) * w.ir_mean) / (math.sqrt(w.ir_power) * w.red_mean)
        spo2 = None
        # 0.02 < r < 1.84 is where the curve below is defined (algorithm_by_RF.cpp)
        if w.correlation >= MIN_SPO2_CORRELATION and 0.02 < r < 1.84:
            # Maxim's generic curve (AN6409), not a calibration for this sensor
            spo2 = (-45.060 * r + 30.354) * r + 94.845
        return Estimate(heart_rate, spo2, r, w.correlation)

    # -- Above RF's 180 BPM -------------------------------------------------------
    def _faster_than_range(self, w, lag):
        """Period (in samples, interpolated) of a pulse faster than MAX_HR,
        or None.

        RF's search starts at the MAX_HR period, so above it RF only sees the
        second autocorrelation peak and reports half the rate. A periodic
        signal repeats best at its own period, so a shorter period that
        repeats at least as well as RF's answer is the pulse, and RF's answer
        a multiple of it.
        """
        if w.correlation <= 0:
            return None
        # One lag past the MAX_HR period: the hill-climb rejects a peak whose
        # next lag falls outside the range it is given
        short = self._fresh_period(w.x, w.ir_power, self._shortest_lag, self._lowest_period + 1)
        if not short:
            return None
        period = self._peak_position(w.x, short)
        if self._fs60 / period <= MAX_HR:
            return None
        if lag and self._acf(w.x, lag) > self._acf(w.x, short):
            return None
        return period

    # -- Below RF's 40 BPM --------------------------------------------------------
    def _slower_than_range(self, w):
        """Whether the window looks like a pulse slower than MIN_HR.

        1. The IR repeats (RF's autocorrelation ratio) at a period between the
           MIN_HR period and one period of the band-pass's lower edge. As in
           RF's search, the first such peak counts.
        2. That period is the signal's own: the spectrum is stronger at
           1/period than at each of its harmonics in the pass band.

        A pulse also repeats every 2nd or 3rd beat, so test 1 alone would take
        a normal pulse whose single-beat repeat is weak for one at half or a
        third of its rate. A periodic signal has no energy below its own rate,
        so that case fails test 2. A slow pulse is a short beat in a long
        period, which puts most of its energy at its own rate, so it passes.
        """
        x, power = w.x, w.ir_power
        acf = {k: self._acf(x, k) for k in range(self._highest_period, self._slowest_period + 2)}
        for lag in range(self._highest_period + 1, self._slowest_period + 1):
            if (acf[lag - 1] < acf[lag] >= acf[lag + 1]
                    and acf[lag] / power >= MIN_AUTOCORRELATION_RATIO):
                break
        else:
            return False
        f = 1.0 / self._peak_position(x, lag)  # cycles per sample
        t = np.arange(len(x))
        # A Hann window keeps leakage from the strong components out of the
        # frequencies compared
        xw = x * np.hanning(len(x))

        def power_at(freq):
            return abs(np.dot(xw, np.exp(-2j * np.pi * freq * t))) ** 2

        own = power_at(f)
        h = 2
        while h * f * self.sample_rate <= BANDPASS_HZ[1]:
            if power_at(h * f) >= own:
                return False
            h += 1
        return True

    def _peak_position(self, x, lag):
        """The autocorrelation peak at `lag`, to a fraction of a sample (a
        parabola through it and its neighbours). Whole-sample lags are
        coarse at 25 samples/s: lags 7 and 8 are 214 and 187 BPM, and lags
        14, 15 and 16 are 107, 100 and 94 BPM."""
        left, mid, right = self._acf(x, lag - 1), self._acf(x, lag), self._acf(x, lag + 1)
        curvature = left - 2 * mid + right
        if curvature >= 0:
            return float(lag)
        return lag + 0.5 * (left - right) / curvature

    # -- RF's period search (algorithm_by_RF.cpp) ------------------------------
    def _detrend(self, x):
        t = np.arange(len(x)) - (len(x) - 1) / 2
        beta = float(np.dot(t, x)) / float(np.dot(t, t))
        return x - beta * t

    def _find_period(self, x, power):
        """The IR period in samples, or 0 if the window isn't periodic enough.

        RF looks for the autocorrelation peak near the previous window's
        period, which keeps it on the right peak in noise. After a fast rise
        in heart rate, though, the old period is close to *twice* the new
        one, and the second autocorrelation peak sits there: RF would then
        report half the heart rate and never let go. So when a fresh search
        from the shortest period finds a valid period well below the
        tracked one, the fresh one wins.
        """
        if self._last_lag == self._lowest_period:
            return self._fresh_period(x, power)
        tracked = self._refine_period(x, self._last_lag, power)
        fresh = self._fresh_period(x, power)
        if fresh and (not tracked or fresh < tracked / 1.5):
            return fresh
        return tracked

    def _fresh_period(self, x, power, lo=None, hi=None):
        lag = self._initial_period(x, power, lo, hi)
        return self._refine_period(x, lag, power, lo, hi) if lag else 0

    def _acf(self, x, lag):
        """Autocorrelation at `lag`, normalised by the overlap length."""
        m = len(x) - lag
        if m <= 0:
            return 0.0
        return float(np.dot(x[:m], x[lag:])) / m

    def _initial_period(self, x, power, lo=None, hi=None):
        """Rough lag of the *first* autocorrelation peak that passes the
        quality ratio, walking right two lags at a time from the shortest
        period (rf_initialize_periodicity_search). Starting at the shortest
        period stops a later peak, a multiple of the true period, from being
        taken for the first. lo/hi default to RF's MAX_HR/MIN_HR periods."""
        lo = lo or self._lowest_period
        hi = hi or self._highest_period
        lag = lo
        aut_right = aut = self._acf(x, lag)
        if aut / power >= MIN_AUTOCORRELATION_RATIO:
            if self._rises_before(x, lag):
                # The autocorrelation already dipped and came back up, so this
                # is the first period's peak, not the lag-0 lobe: HR is near
                # MAX_HR. Walking down from here would land on the second
                # peak and report half the heart rate.
                return lag
            # Still on the lag-0 lobe: walk down off it
            while True:
                aut = aut_right
                lag += 2
                aut_right = self._acf(x, lag)
                if not (aut_right / power >= MIN_AUTOCORRELATION_RATIO and aut_right < aut
                        and lag <= hi):
                    break
            if lag > hi:
                return 0
        while True:
            lag += 2
            aut_right = self._acf(x, lag)
            if not (aut_right / power < MIN_AUTOCORRELATION_RATIO and lag <= hi):
                break
        return 0 if lag > hi else lag

    def _rises_before(self, x, lag):
        """Whether the autocorrelation increases anywhere between lag 1 and
        `lag`, i.e. it is past the lobe around lag 0."""
        acf = [self._acf(x, k) for k in range(1, lag + 1)]
        return any(b > a for a, b in zip(acf, acf[1:]))

    def _refine_period(self, x, start, power, lo=None, hi=None):
        """Hill-climb from `start` to the local autocorrelation maximum, and
        accept it only if it is at least MIN_AUTOCORRELATION_RATIO of the
        lag-0 value (rf_signal_periodicity)."""
        lo = lo or self._lowest_period
        hi = hi or self._highest_period
        lag = start
        aut_save = aut = aut_left = self._acf(x, lag)
        while True:
            aut = aut_left
            lag -= 1
            aut_left = self._acf(x, lag)
            if not (aut_left > aut and lag >= lo):
                break
        left_limit_reached = lag < lo
        if left_limit_reached:
            lag = start
            aut = aut_save
        else:
            lag += 1
        if lag == start:
            aut_right = aut
            while True:
                aut = aut_right
                lag += 1
                aut_right = self._acf(x, lag)
                if not (aut_right > aut and lag <= hi):
                    break
            lag = 0 if lag > hi else lag - 1
            if lag == start and left_limit_reached:
                lag = 0
        if aut / power < MIN_AUTOCORRELATION_RATIO:
            return 0
        return lag
