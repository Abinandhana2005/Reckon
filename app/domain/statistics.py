"""Baselines for "normal for this stock".

We deliberately avoid both a fixed percentage threshold and a fitted model.

A fixed threshold ("flag anything beyond 2%") cannot be defended: 2% is a large
move for one stock and an ordinary day for another. A regression on the market
gives an unstable coefficient on 60 daily observations, and a two-factor version
on market plus sector is multicollinear because sector indices track the market
closely.

Instead a move is compared against the same stock's own history of the same
quantity, and expressed as a percentile. That assumes no distribution, needs no
trimming, and translates into a sentence a reader can check: this move is larger
than 90% of what this stock normally does over this many sessions.
"""

from __future__ import annotations

WINDOW_COUNT = 60
"""Rolling windows used to build a baseline.

Sets the resolution of the percentile: with 60 observations the 90th percentile
is estimated from 6 points and the 95th from 3, which is why the threshold sits
at 90 rather than higher.
"""

UNUSUAL_PERCENTILE = 90.0

MIN_HISTORY_BARS = WINDOW_COUNT + 10 - 1
"""Bars needed for the longest gap we will judge (10 sessions)."""


def window_returns(closes: list[float], length: int) -> list[float]:
    """Every overlapping `length`-session return in `closes`, oldest first.

    Overlapping windows are autocorrelated. That is acceptable here because the
    result is used descriptively — where today sits among this stock's own past
    behaviour — and not as a significance test.
    """
    if length < 1:
        raise ValueError("window length must be at least 1")
    if len(closes) <= length:
        return []
    return [
        closes[i + length] / closes[i] - 1.0
        for i in range(len(closes) - length)
        if closes[i] > 0
    ]


def paired_differences(left: list[float], right: list[float]) -> list[float]:
    if len(left) != len(right):
        raise ValueError("series must be the same length to be differenced")
    return [a - b for a, b in zip(left, right)]


def percentile_rank(observed: float, history: list[float]) -> float:
    """Where |observed| sits in the distribution of |history|, as 0-100.

    Absolute values throughout: a move is unusual by size, in either direction.
    """
    if not history:
        raise ValueError("cannot rank against an empty history")
    magnitude = abs(observed)
    below = sum(1 for value in history if abs(value) < magnitude)
    return 100.0 * below / len(history)


def typical_magnitude(history: list[float]) -> float:
    """The median absolute value, used only to phrase the baseline for a reader.

    Never used to decide a verdict — the percentile does that — but "this stock
    usually stays within 1.3%" is more useful on a card than a percentile.
    """
    if not history:
        raise ValueError("cannot summarise an empty history")
    magnitudes = sorted(abs(value) for value in history)
    middle = len(magnitudes) // 2
    if len(magnitudes) % 2 == 1:
        return magnitudes[middle]
    return (magnitudes[middle - 1] + magnitudes[middle]) / 2.0
