"""One-sided t tail, Newey-West HAC t, and multiple-testing adjustments.

The t tail uses the regularized incomplete beta identity. Adjusted p-values
are reported for every pre-registered config; the spec chooses which family
gates selection. The gate uses the larger of the iid t p-value and the HAC
p-value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from research.harness.errors import HarnessError

# Lanczos coefficients for log-gamma. g = 7, so there are g + 2 coefficients.
_LANCZOS_G = 7
_LANCZOS: tuple[float, ...] = (
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
)


def log_gamma(z: float) -> float:
    """Natural log of the gamma function for positive z."""

    if z <= 0.0:
        raise HarnessError("stats", "log-gamma is only defined here for positive z.")
    if z < 0.5:
        return math.log(math.pi / math.sin(math.pi * z)) - log_gamma(1.0 - z)
    z_shift = z - 1.0
    total = _LANCZOS[0]
    for index, coeff in enumerate(_LANCZOS[1:], start=1):
        total += coeff / (z_shift + index)
    t_value = z_shift + _LANCZOS_G + 0.5
    return (
        0.5 * math.log(2.0 * math.pi)
        + (z_shift + 0.5) * math.log(t_value)
        - t_value
        + math.log(total)
    )


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b), the regularized incomplete beta function."""

    if a <= 0.0 or b <= 0.0:
        raise HarnessError("stats", "incomplete beta requires positive a and b.")
    if x < 0.0 or x > 1.0:
        raise HarnessError("stats", "incomplete beta x must lie in [0, 1].")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    log_prefix = (
        log_gamma(a + b) - log_gamma(a) - log_gamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    )
    prefix = math.exp(log_prefix)
    if x < (a + 1.0) / (a + b + 2.0):
        return prefix * _beta_continued_fraction(a, b, x) / a
    return 1.0 - prefix * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_upper_tail(t_stat: float, degrees: int) -> float:
    """One-sided upper tail P(T >= t_stat) for Student's t with `degrees` df.

    For t_stat >= 0 this is half the two-tailed probability
    I_x(df/2, 1/2) with x = df / (df + t^2). Negative statistics return the
    complementary probability, which is the correct tail for H1: mean > 0.
    """

    if degrees < 1:
        raise HarnessError("stats", "t degrees of freedom must be at least 1.")
    if t_stat == 0.0:
        return 0.5
    x_value = degrees / (degrees + t_stat * t_stat)
    two_tailed = regularized_incomplete_beta(degrees / 2.0, 0.5, x_value)
    if t_stat > 0.0:
        return two_tailed / 2.0
    return 1.0 - two_tailed / 2.0


def newey_west_mean_test(values: Sequence[float]) -> tuple[float | None, float | None, int | None]:
    """One-sided HAC t-test of H1: mean > 0.

    Bartlett kernel. The lag is ``floor(4 * (n / 100) ** (2 / 9))``, capped
    at ``n - 1``. Returns ``(t_stat, p_value, lag)``. A non-positive HAC
    variance does not reject (p = 1) unless every observation is identical,
    in which case the same degenerate rule as the iid t-test applies.
    """

    count = len(values)
    if count < 2:
        return None, None, None
    mean = math.fsum(values) / count
    demeaned = [value - mean for value in values]
    gamma0 = math.fsum(value * value for value in demeaned) / count
    lag = _newey_west_lag(count)
    if gamma0 == 0.0:
        return None, _degenerate_upper_tail(mean), lag
    hac = gamma0
    for lag_index in range(1, lag + 1):
        weight = 1.0 - lag_index / (lag + 1.0)
        gamma = (
            math.fsum(
                demeaned[index] * demeaned[index - lag_index] for index in range(lag_index, count)
            )
            / count
        )
        hac += 2.0 * weight * gamma
    if hac <= 0.0:
        return None, 1.0, lag
    standard_error = math.sqrt(hac / count)
    if standard_error == 0.0:
        return None, 1.0, lag
    t_stat = mean / standard_error
    return t_stat, student_t_upper_tail(t_stat, count - 1), lag


def bonferroni(p_values: list[float]) -> list[float]:
    """Bonferroni adjusted p-values, capped at 1."""

    _require_p_values(p_values)
    family = len(p_values)
    return [min(1.0, p_value * family) for p_value in p_values]


def holm(p_values: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, monotone and capped at 1."""

    _require_p_values(p_values)
    family = len(p_values)
    order = sorted(range(family), key=lambda index: (p_values[index], index))
    adjusted = [0.0] * family
    running = 0.0
    for rank, index in enumerate(order):
        raw = p_values[index] * (family - rank)
        running = max(running, raw)
        adjusted[index] = min(1.0, running)
    return adjusted


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, monotone from the largest p."""

    _require_p_values(p_values)
    family = len(p_values)
    order = sorted(range(family), key=lambda index: (p_values[index], index))
    raw = [0.0] * family
    for rank, index in enumerate(order):
        raw[index] = p_values[index] * family / (rank + 1)
    adjusted = [0.0] * family
    running = 1.0
    for index in reversed(order):
        running = min(running, raw[index])
        adjusted[index] = min(1.0, running)
    return adjusted


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    max_iter = 200
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c_value = 1.0
    d_value = 1.0 - qab * x / qap
    if abs(d_value) < tiny:
        d_value = tiny
    d_value = 1.0 / d_value
    h_value = d_value
    for m_index in range(1, max_iter + 1):
        m2 = 2 * m_index
        numerator = m_index * (b - m_index) * x / ((qam + m2) * (a + m2))
        d_value = 1.0 + numerator * d_value
        if abs(d_value) < tiny:
            d_value = tiny
        c_value = 1.0 + numerator / c_value
        if abs(c_value) < tiny:
            c_value = tiny
        d_value = 1.0 / d_value
        h_value *= d_value * c_value
        numerator = -(a + m_index) * (qab + m_index) * x / ((a + m2) * (qap + m2))
        d_value = 1.0 + numerator * d_value
        if abs(d_value) < tiny:
            d_value = tiny
        c_value = 1.0 + numerator / c_value
        if abs(c_value) < tiny:
            c_value = tiny
        d_value = 1.0 / d_value
        delta = d_value * c_value
        h_value *= delta
        if abs(delta - 1.0) < epsilon:
            return h_value
    raise HarnessError("stats", "incomplete beta continued fraction did not converge.")


def _newey_west_lag(count: int) -> int:
    raw = math.floor(4.0 * (count / 100.0) ** (2.0 / 9.0))
    return max(0, min(count - 1, int(raw)))


def _degenerate_upper_tail(mean: float) -> float:
    if mean > 0.0:
        return 0.0
    if mean < 0.0:
        return 1.0
    return 0.5


def _require_p_values(p_values: list[float]) -> None:
    if not p_values:
        raise HarnessError("stats", "multiple testing requires at least one p-value.")
    for p_value in p_values:
        if not math.isfinite(p_value) or p_value < 0.0 or p_value > 1.0:
            raise HarnessError("stats", "p-values must lie in [0, 1].")
