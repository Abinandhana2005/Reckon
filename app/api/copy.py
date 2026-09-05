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


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(value: float) -> str:
    """A percentile as English reads it: 1st, 22nd, 53rd, 11th.

    Appending "th" to every number is a small thing that makes a careful
    document look careless, and this text is the product's evidence.
    """
    number = int(round(value))
    if 11 <= number % 100 <= 13:
        return f"{number}th"
    suffix = _ORDINAL_SUFFIX.get(number % 10, "th")
    return f"{number}{suffix}"


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
            f"Its move ranks at the {ordinal(own.percentile)} percentile of its own "
            f"{own.sample_size} comparable spans, and its gap to the market ranks at the "
            f"{ordinal(evidence.vs_market.percentile)}. Neither is unusual."
        )

    if item.verdict is Verdict.WITH_MARKET:
        return (
            f"Its move was unusual for this stock, but the gap to the market ranks at the "
            f"{ordinal(evidence.vs_market.percentile)} percentile of "
            f"{evidence.vs_market.sample_size} comparable spans, which is ordinary. "
            f"{COMPARISON_DISCLAIMER}"
        )

    label = sector_name or evidence.sector_index
    return (
        f"Its move was unusual against the market, but the gap to {label} ranks at the "
        f"{ordinal(evidence.vs_sector.percentile)} percentile of "
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

# --------------------------------------------------------------------------
# The silence report: what was checked and not surfaced, in real numbers.
# --------------------------------------------------------------------------


def silence_report(counts: dict[str, int]) -> str:
    """One sentence accounting for every symbol that was not surfaced.

    The product's claim is that nothing was filtered out, so the clauses are
    built from the same counts the sections are built from. A clause is omitted
    only when its count is zero, never to shorten the sentence.
    """
    checked = counts["checked"]
    stocks = "stock" if checked == 1 else "stocks"
    clauses: list[str] = []
    explained = counts["explained"]
    if explained:
        clauses.append(
            f"{explained} moved with the market or its sector"
            if explained > 1
            else "1 moved with the market or its sector"
        )
    if counts["quiet"]:
        clauses.append(f"{counts['quiet']} {'was' if counts['quiet'] == 1 else 'were'} quiet")
    if counts["cant_say"]:
        clauses.append(f"{counts['cant_say']} could not be evaluated")
    if counts["needs_you"]:
        clauses.append(
            f"{counts['needs_you']} {'needs' if counts['needs_you'] == 1 else 'need'} your attention"
        )

    if not clauses:
        return f"{checked} {stocks} checked."
    return f"{checked} {stocks} checked. " + _joined(clauses) + "."


def _joined(clauses: list[str]) -> str:
    if len(clauses) == 1:
        return clauses[0]
    return f"{', '.join(clauses[:-1])} and {clauses[-1]}"


def start_here_line(item: Classification) -> str:
    """Why this one item is at the top, stated as what the classifier did.

    No score is invented. The brief already orders what needs attention by the
    size of the move, so the first of those is the item, and this says so.
    """
    if item.verdict is Verdict.EVENT:
        return "A confirmed event outranks every comparison, so this is the first thing to look at."
    return (
        "This is the largest move on your watchlist that neither the market nor "
        "its sector accounts for."
    )


UNCERTAINTY_LABEL = {
    Reason.UNTRUSTED_QUOTE: "Price could not be trusted",
    Reason.INSUFFICIENT_HISTORY: "Not enough history",
    Reason.UNADJUSTABLE_ACTION: "Corporate action could not be adjusted",
}

UNCERTAINTY_DETAIL = {
    Reason.UNTRUSTED_QUOTE: (
        "The latest price was stale, disputed or unavailable, so no comparison was attempted."
    ),
    Reason.INSUFFICIENT_HISTORY: (
        "Fewer sessions are on record than a normal range can be built from."
    ),
    Reason.UNADJUSTABLE_ACTION: (
        "A split, bonus or dividend fell in this window and the factor to restate "
        "the reference price could not be determined."
    ),
}


def uncertainty_label(reason: Reason) -> str:
    return UNCERTAINTY_LABEL.get(reason, "Could not be evaluated")


def uncertainty_detail(reason: Reason) -> str:
    return UNCERTAINTY_DETAIL.get(reason, "No comparison is offered.")


# --------------------------------------------------------------------------
# The decision trace: the checks that ran, in the order they ran.
# --------------------------------------------------------------------------

PASSED = "PASSED"
"""The check ran and did not end the classification."""

DECIDED = "DECIDED"
"""The check ran and produced the verdict."""

SKIPPED = "SKIPPED"
"""The check never ran, because an earlier one had already decided."""

UNAVAILABLE = "UNAVAILABLE"
"""The check could not run because the data it needs is not held."""


def decision_trace(item: Classification, *, sector_name: str | None = None) -> list[dict]:
    """Replay the classifier's precedence for one symbol, as a reader can follow it.

    Derived from the retained evidence rather than recomputed, so what is shown
    is what actually happened. The order is the classifier's own: doubt about
    the data first, then facts, then the symbol's own range, then the market,
    then the sector.
    """
    evidence = item.evidence
    verdict = item.verdict
    decided_early = verdict is Verdict.CANT_SAY
    steps: list[dict] = []

    steps.append(_step(
        "data",
        "Data quality",
        DECIDED if decided_early else PASSED,
        _CANT_SAY_DETAILS.get(item.reason, "No comparison is offered.")
        if decided_early
        else f"A trusted price and {evidence.history_bars} sessions of history were on record.",
    ))

    if decided_early:
        steps += [
            _step("event", "Event check", SKIPPED, "Not reached."),
            _step("own", "Own movement", SKIPPED, "Not reached."),
            _step("market", "Market comparison", SKIPPED, "Not reached."),
            _step("sector", "Sector comparison", SKIPPED, "Not reached."),
            _step("verdict", "Final verdict", DECIDED, headline(item, sector_name=sector_name)),
        ]
        return steps

    events = evidence.events_in_window or evidence.events_upcoming
    steps.append(_step(
        "event",
        "Event check",
        DECIDED if verdict is Verdict.EVENT else PASSED,
        events[0].detail if verdict is Verdict.EVENT and events
        else "No corporate action is on record for this window.",
    ))

    if verdict is Verdict.EVENT:
        steps += [
            _step("own", "Own movement", SKIPPED, "A recorded event outranks every comparison."),
            _step("market", "Market comparison", SKIPPED, "Not reached."),
            _step("sector", "Sector comparison", SKIPPED, "Not reached."),
            _step("verdict", "Final verdict", DECIDED, headline(item, sector_name=sector_name)),
        ]
        return steps

    own = evidence.own
    steps.append(_step(
        "own",
        "Own movement",
        PASSED,
        f"It {movement(evidence.adjusted_return)}, which ranks at the "
        f"{ordinal(own.percentile)} percentile of its own {own.sample_size} comparable spans."
        if own
        else "No baseline was available.",
    ))

    market = evidence.vs_market
    steps.append(_step(
        "market",
        "Market comparison",
        DECIDED if verdict in (Verdict.WITH_MARKET, Verdict.QUIET) else PASSED,
        f"The market {movement(evidence.market_return)}. The gap ranks at the "
        f"{ordinal(market.percentile)} percentile, which is "
        f"{'ordinary' if market.percentile < 90 else 'unusual'}."
        if market
        else "No market comparison was available.",
    ))

    sector = evidence.vs_sector
    if not evidence.sector_available or sector is None:
        sector_note = "No sector index is held for this stock, so this comparison was not available."
        sector_status = UNAVAILABLE
    else:
        sector_note = (
            f"{sector_name or evidence.sector_index} "
            f"{movement(evidence.sector_return)}. The gap ranks at the "
            f"{ordinal(sector.percentile)} percentile, which is "
            f"{'ordinary' if sector.percentile < 90 else 'unusual'}."
        )
        sector_status = DECIDED if verdict is Verdict.WITH_SECTOR else PASSED
    if verdict in (Verdict.WITH_MARKET, Verdict.QUIET):
        sector_status = SKIPPED
        sector_note = "Not reached: the market comparison had already accounted for the move."
    steps.append(_step("sector", "Sector comparison", sector_status, sector_note))

    steps.append(_step(
        "verdict", "Final verdict", DECIDED, headline(item, sector_name=sector_name)
    ))
    return steps


def _step(key: str, label: str, status: str, note: str) -> dict:
    return {"key": key, "label": label, "status": status, "note": note}
