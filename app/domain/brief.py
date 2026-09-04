"""Group classified symbols into the four things a brief shows.

Grouping is not filtering. Every classification handed in comes out again in
exactly one bucket, and `Brief.accounted_for` proves it. That invariant is what
lets the app claim "16 checked, 3 need you" and then answer for the other 13.

The unit of the Explained section is the explanation, not the stock: symbols
that moved with the same sector collapse into one line naming that sector. The
grouping is structural only -- no wording is decided here, because the wording
has to be reviewable for causal language on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.verdicts import Classification, Verdict

NEEDS_YOU = frozenset({Verdict.EVENT, Verdict.UNEXPLAINED})
EXPLAINED = frozenset({Verdict.WITH_MARKET, Verdict.WITH_SECTOR})


@dataclass(frozen=True)
class SectorGroup:
    """Symbols whose move was consistent with the same sector index."""

    sector_index: str
    index_return: float | None
    members: list[Classification]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class Brief:
    needs_you: list[Classification] = field(default_factory=list)
    with_market: list[Classification] = field(default_factory=list)
    sector_groups: list[SectorGroup] = field(default_factory=list)
    quiet: list[Classification] = field(default_factory=list)
    cant_say: list[Classification] = field(default_factory=list)
    market_return: float | None = None

    @property
    def explained(self) -> list[Classification]:
        return self.with_market + [m for g in self.sector_groups for m in g.members]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "checked": self.accounted_for,
            "needs_you": len(self.needs_you),
            "explained": len(self.explained),
            "quiet": len(self.quiet),
            "cant_say": len(self.cant_say),
        }

    @property
    def accounted_for(self) -> int:
        return (
            len(self.needs_you) + len(self.explained) + len(self.quiet) + len(self.cant_say)
        )


def _urgency(item: Classification) -> tuple[float, str]:
    """Largest move first, and alphabetical within a tie so order is stable."""
    move = item.evidence.adjusted_return
    return (-abs(move) if move is not None else 0.0, item.symbol)


def assemble(
    classifications: list[Classification],
    *,
    market_return: float | None = None,
    sector_returns: dict[str, float | None] | None = None,
) -> Brief:
    returns = sector_returns or {}
    needs_you: list[Classification] = []
    with_market: list[Classification] = []
    quiet: list[Classification] = []
    cant_say: list[Classification] = []
    by_sector: dict[str, list[Classification]] = {}

    for item in classifications:
        if item.verdict in NEEDS_YOU:
            needs_you.append(item)
        elif item.verdict is Verdict.WITH_MARKET:
            with_market.append(item)
        elif item.verdict is Verdict.WITH_SECTOR:
            # A WITH_SECTOR verdict is only reachable with a sector index, so
            # the key is always present; falling back would hide a real bug.
            by_sector.setdefault(item.evidence.sector_index, []).append(item)
        elif item.verdict is Verdict.QUIET:
            quiet.append(item)
        else:
            cant_say.append(item)

    groups = [
        SectorGroup(
            sector_index=code,
            index_return=returns.get(code),
            members=sorted(members, key=_urgency),
        )
        for code, members in by_sector.items()
    ]

    return Brief(
        needs_you=sorted(needs_you, key=_urgency),
        with_market=sorted(with_market, key=_urgency),
        sector_groups=sorted(groups, key=lambda g: (-g.size, g.sector_index)),
        quiet=sorted(quiet, key=lambda c: c.symbol),
        cant_say=sorted(cant_say, key=lambda c: c.symbol),
        market_return=market_return,
    )
