"""Deterministic statistical calculations used by the verdict engine."""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Tuple


CALCULATION_VERSION = "wilson-two-sided-v1"


def wilson_interval(
    success_count: int, sample_count: int, confidence_level: float = 0.95
) -> Tuple[float, float]:
    """Return a two-sided Wilson score confidence interval for a proportion."""

    if sample_count <= 0:
        raise ValueError("sample_count must be greater than zero.")
    if success_count < 0 or success_count > sample_count:
        raise ValueError("success_count must be between zero and sample_count.")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between zero and one.")

    proportion = success_count / sample_count
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    z_squared = z * z
    denominator = 1.0 + z_squared / sample_count
    center = (proportion + z_squared / (2.0 * sample_count)) / denominator
    margin = (
        z
        * math.sqrt(
            (proportion * (1.0 - proportion) + z_squared / (4.0 * sample_count))
            / sample_count
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def wilson_lower_bound(
    success_count: int, sample_count: int, confidence_level: float = 0.95
) -> float:
    """Return the lower endpoint of a two-sided Wilson score interval."""

    return wilson_interval(success_count, sample_count, confidence_level)[0]
