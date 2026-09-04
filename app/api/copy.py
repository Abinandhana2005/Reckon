"""User-facing wording for verdicts and groups.

Deliberately outside app/domain. The classifier stays a pure function over
numbers, and the sentences a regulator would read stay in one file that can be
reviewed on its own for two failures that matter in a financial product:

  Causal claims.  A percentile comparison says two things moved together. It
  does not say one moved the other, so no template contains "because", "caused"
  or "due to", and every WITH_* card carries an explicit disclaimer.

  Advice.  Nothing here tells anyone what to do with a position.

BANNED_WORDS is asserted against every string this module can produce, so a
template that acquires either failure fails the suite rather than shipping.
"""

from __future__ import annotations

from app.domain.verdicts import Classification, Epistemic, Reason, Verdict

BANNED_WORDS = frozenset(
    {
        "because",
        "caused",
        "causes",
        "due",
        "should",
        "buy",
        "sell",
        "opportunity",
        "undervalued",
        "overvalued",
        "momentum",
        "breakout",
        "bullish",
        "bearish",
        "recommend",
    }
)

COMPARISON_DISCLAIMER = "This is a comparison, not a cause."

EPISTEMIC_LABEL = {
    Epistemic.KNOWN: "Known",
    Epistemic.INFERRED: "Inferred",
    Epistemic.UNKNOWN: "Unknown",
}

_CANT_SAY_HEADLINES = {
    Reason.UNTRUSTED_QUOTE: "We can't check this right now.",
    Reason.INSUFFICIENT_HISTORY: "Not enough history to compare against.",
    Reason.UNADJUSTABLE_ACTION: "A corporate action here can't be adjusted.",
}

_CANT_SAY_DETAILS = {
    Reason.UNTRUSTED_QUOTE: "The latest price is stale or disputed, so no comparison is offered.",
    Reason.INSUFFICIENT_HISTORY: "This symbol has too few sessions to establish a normal range.",
    Reason.UNADJUSTABLE_ACTION: "Without a usable adjustment factor any change would be misleading.",
}


def percent(value: float | None, places: int = 1) -> str:
    if value is None:
        return "unavailable"
    return f"{value * 100:.{places}f}%"


def _moved(value: float | None) -> str:
    """Direction as plain description. 'rose'/'fell' report, they do not explain."""
    if value is None:
        return "moved"
    if value > 0:
        return "rose"
    if value < 0:
        return "fell"
    return "was flat"


def movement(value: float | None) -> str:
    if value is None:
        return "moved by an unknown amount"
    if value == 0:
        return "was flat"
    return f"{_moved(value)} {percent(abs(value))}"


def headline(item: Classification, *, sector_name: str | None = None) -> str:
    evidence = item.evidence

    if item.verdict is Verdict.CANT_SAY:
        return _CANT_SAY_HEADLINES.get(item.reason, "We can't say.")

    if item.verdict is Verdict.EVENT:
        events = (
            evidence.events_in_window
            if item.reason is Reason.EVENT_OCCURRED
            else evidence.events_upcoming
        )
        detail = events[0].detail if events else "A corporate event is on record."
        return detail if item.reason is Reason.EVENT_OCCURRED else f"{detail} (scheduled)"

    if item.verdict is Verdict.QUIET:
        return "Normal for this stock."
    if item.verdict is Verdict.WITH_MARKET:
        return "Consistent with the market."
    if item.verdict is Verdict.WITH_SECTOR:
        return f"Consistent with {sector_name or evidence.sector_index}."
    return "Unusual, and not explained by the market or its sector."


def detail(item: Classification, *, sector_name: str | None = None) -> str:
    evidence = item.evidence

    if item.verdict is Verdict.CANT_SAY:
        return _CANT_SAY_DETAILS.get(item.reason, "No comparison is offered.")

    own = movement(evidence.adjusted_return)

    if item.verdict is Verdict.EVENT:
        if item.reason is Reason.EVENT_UPCOMING:
            return f"On record for the next few sessions. The price {own} since you last looked."
        adjusted = (
            " The change shown is adjusted for it."
            if evidence.adjustment_factor != 1.0
            else ""
        )
        return f"Recorded since you last looked. The price {own}.{adjusted}"

    if item.verdict is Verdict.QUIET:
        return (
            f"It {own}, which is within its usual range and close to the market's move."
        )

    if item.verdict is Verdict.WITH_MARKET:
        return (
            f"It {own}; the market {movement(evidence.market_return)}. "
            f"The gap between them is normal for this stock. {COMPARISON_DISCLAIMER}"
        )

    if item.verdict is Verdict.WITH_SECTOR:
        label = sector_name or evidence.sector_index
        return (
            f"It {own}; {label} {movement(evidence.sector_return)}, "
            f"while the market {movement(evidence.market_return)}. {COMPARISON_DISCLAIMER}"
        )

    typical = evidence.own.typical_abs if evidence.own else None
    typical_text = f" Its usual move over this span is about {percent(typical)}." if typical else ""
    return (
        f"It {own}, which is unusual relative to its own range, and the gap to "
        f"both the market and its sector is unusual too.{typical_text} "
        "We don't know why."
    )


def why_not_flagged(item: Classification, *, sector_name: str | None = None) -> str:
    """The answer to "why didn't you flag this?", from retained evidence."""
    if item.verdict in (Verdict.EVENT, Verdict.UNEXPLAINED):
        return "This was surfaced."

    evidence = item.evidence
    if item.verdict is Verdict.CANT_SAY:
        return (
            "Nothing was claimed here. "
            f"{_CANT_SAY_DETAILS.get(item.reason, 'No comparison is offered.')}"
        )

    if item.verdict is Verdict.QUIET:
        own = evidence.own
        return (
            f"Its move ranks at the {own.percentile:.0f}th percentile of its own "
            f"{own.sample_size} comparable spans, and its gap to the market ranks at the "
            f"{evidence.vs_market.percentile:.0f}th. Neither is unusual."
        )

    if item.verdict is Verdict.WITH_MARKET:
        return (
            f"Its move was unusual for this stock, but the gap to the market ranks at the "
            f"{evidence.vs_market.percentile:.0f}th percentile of "
            f"{evidence.vs_market.sample_size} comparable spans, which is ordinary. "
            f"{COMPARISON_DISCLAIMER}"
        )

    label = sector_name or evidence.sector_index
    return (
        f"Its move was unusual against the market, but the gap to {label} ranks at the "
        f"{evidence.vs_sector.percentile:.0f}th percentile of "
        f"{evidence.vs_sector.sample_size} comparable spans, which is ordinary. "
        f"{COMPARISON_DISCLAIMER}"
    )


def sector_group_line(sector_name: str, index_return: float | None, size: int) -> str:
    stocks = "stock" if size == 1 else "stocks"
    return f"{sector_name} {movement(index_return)}. {size} of your {stocks} moved with it."


def market_group_line(market_return: float | None, size: int) -> str:
    others = "one" if size == 1 else str(size)
    verb = "stock" if size == 1 else "others"
    if size == 1:
        return f"One stock moved with the market, which {movement(market_return)}."
    return f"{others} {verb} moved with the market, which {movement(market_return)}."


def quiet_line(size: int) -> str:
    return f"{size} quiet." if size != 1 else "1 quiet."


def cant_say_line(size: int) -> str:
    subject = "symbol" if size == 1 else "symbols"
    return f"Couldn't check {size} {subject}."


def accounting_line(counts: dict[str, int]) -> str:
    checked = counts["checked"]
    needs = counts["needs_you"]
    subject = "stock" if checked == 1 else "stocks"
    if needs == 0:
        return f"All {checked} {subject} accounted for. Nothing needs you."
    return f"{checked} {subject} checked. {needs} need you."
