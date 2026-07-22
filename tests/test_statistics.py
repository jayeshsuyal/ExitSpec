import pytest

from exitspec.statistics import wilson_interval, wilson_lower_bound


def test_wilson_bound_for_passing_brick_one_result():
    assert wilson_lower_bound(197, 200) == pytest.approx(0.9568342712)


def test_wilson_bound_for_inconclusive_brick_one_result():
    assert wilson_lower_bound(196, 200) == pytest.approx(0.9497128709)


def test_wilson_interval_stays_inside_probability_range():
    lower, upper = wilson_interval(0, 200)

    assert 0.0 <= lower <= upper <= 1.0


@pytest.mark.parametrize(
    "success_count,sample_count",
    [(-1, 10), (11, 10), (0, 0)],
)
def test_wilson_rejects_invalid_counts(success_count, sample_count):
    with pytest.raises(ValueError):
        wilson_lower_bound(success_count, sample_count)
