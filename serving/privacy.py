"""A disjoint-cell release policy, shared by the exporter and its tests.

Only district/disease/week cases are released as epidemiological observations.
There are no exact parent totals, report counts, admissions, deaths or overlapping
time aggregates with which to subtract a suppressed cell. Published positive
counts are rounded DOWN to a multiple of five; 1--4 are withheld. Models and
logistics quantities use the same conservative display bins. For linked balance
measures, one withheld quantity withholds the entire balance tuple.

This prevents the specific small-cell and complementary-total disclosure paths;
it is not a differential-privacy guarantee against arbitrary external knowledge.
"""
from __future__ import annotations

import math

K = 5


def count_bin(value):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    if number == 0:
        return 0
    if number < K:
        return None
    return int(number // K) * K


def coupled_bins(values):
    """Withhold a whole arithmetic family if any positive component is small."""
    binned = [count_bin(value) for value in values]
    if any(value is None for value in binned):
        return [None] * len(binned)
    return binned


def safe_ratio(value, precision=3):
    if value is None:
        return None
    number = float(value)
    return round(number, precision) if math.isfinite(number) else None


def displayed_alert(score, threshold_multiplier, complete=True):
    """Signals may be recomputed only for complete, non-suppressed releases."""
    return bool(complete and score is not None and score >= threshold_multiplier)
