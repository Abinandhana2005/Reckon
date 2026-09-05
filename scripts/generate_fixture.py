"""Generate the deterministic market fixture the demo and tests both run on.

The data is synthetic and the issuers are fictional. That is a deliberate
choice, not a shortcut. Exchange market data is licensed and NSE states that
data received from the exchange or an authorised vendor is for internal use and
not for redistribution, so committing real prices to a public repository and
serving them from a public URL is not something this project should do. Showing
invented prices under a real company's name would be worse still.

Generating instead buys two further things: the scenarios are designed rather
than hunted for, and the same seed produces the same market every time, so a
test and a demo can share one artifact.

Prices come from a three-level factor model — market, sector, idiosyncratic —
with volatilities in the range published for Indian equities. The classifier
never sees these parameters; it has to infer structure from prices alone.

    python -m scripts.generate_fixture --out fixtures/market.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

SEED = 20260904
SESSIONS = 150
MARKET_INDEX = "MKTIDX"

MARKET_DAILY_VOL = 0.009
SECTOR_EXCESS_VOL = 0.007
IDIO_VOL_DEFAULT = 0.013


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    name: str
    sector_index: str | None
    base_price: float
    idio_vol: float = IDIO_VOL_DEFAULT
    listed_after: int = 0


SECTORS = {
    "BANKIDX": "Banking",
    "ITIDX": "Information Technology",
    "PHARMAIDX": "Pharmaceuticals",
    "ENERGYIDX": "Energy",
    "FMCGIDX": "Consumer Goods",
}

SYMBOLS = [
    SymbolSpec("MRDB", "Meridian Bank", "BANKIDX", 1420.0, 0.011),
    SymbolSpec("KVRB", "Kaveri Bank", "BANKIDX", 640.0, 0.014),
    SymbolSpec("SGMF", "Sangam Finance", "BANKIDX", 288.0, 0.017),
    SymbolSpec("NWTC", "Northwind Technologies", "ITIDX", 3180.0, 0.012),
    SymbolSpec("ORBS", "Orbit Systems", "ITIDX", 1075.0, 0.015),
    SymbolSpec("PRXD", "Praxis Digital", "ITIDX", 412.0, 0.019),
    SymbolSpec("VNTP", "Vantara Pharma", "PHARMAIDX", 2260.0, 0.010),
    SymbolSpec("SETL", "Setu Laboratories", "PHARMAIDX", 890.0, 0.013),
    SymbolSpec("ARDB", "Ardent Bio", "PHARMAIDX", 154.0, 0.022, listed_after=110),
    SymbolSpec("DCNP", "Deccan Power", "ENERGYIDX", 505.0, 0.011),
    SymbolSpec("KLSE", "Kailash Energy", "ENERGYIDX", 1730.0, 0.013),
    SymbolSpec("TRFL", "Terra Fuels", "ENERGYIDX", 246.0, 0.016),
    SymbolSpec("MRGF", "Marigold Foods", "FMCGIDX", 2940.0, 0.008),
    SymbolSpec("SHDC", "Sahyadri Consumer", "FMCGIDX", 1185.0, 0.009),
    SymbolSpec("BLWG", "Bellwether Goods", "FMCGIDX", 720.0, 0.010),
    SymbolSpec("ZNTH", "Zenith Holdings", None, 960.0, 0.014),
]

# Sessions counted from the end: 149 is the latest, so -1 is today.
TODAY = SESSIONS - 1

# A user who was away five sessions is compared against a five-session window, so the
# scenarios have to be far enough apart that one window holds exactly one scenario.
# Four consecutive scenario days meant a five-session window ending on the sector fall
# also swallowed the market crash, and the sector move stopped being separable from it.
SCENARIO_SPACING = 8

RALLY_DAY = TODAY - 1
ANOMALY_DAY = RALLY_DAY - SCENARIO_SPACING
SECTOR_SHOCK_DAY = ANOMALY_DAY - SCENARIO_SPACING
MARKET_SHOCK_DAY = SECTOR_SHOCK_DAY - SCENARIO_SPACING

SCENARIOS = {
    MARKET_SHOCK_DAY: "market-wide fall",
    SECTOR_SHOCK_DAY: "banking sector fall on a flat market",
    ANOMALY_DAY: "single-stock move with no event and no common factor",
    RALLY_DAY: "market rally that one stock does not participate in",
}

MARKET_SHOCK = -0.036
SECTOR_SHOCK = ("BANKIDX", -0.040)
ANOMALY = ("VNTP", 0.031)
RALLY = 0.025
RALLY_NON_PARTICIPANT = "ZNTH"

# Each scenario builds over the sessions before it rather than arriving as a one-day
# spike. A spike is invisible to anyone who was away: over five sessions it is averaged
# against four ordinary days and lands inside the stock's normal range, so the classifier
# correctly calls it QUIET and the demo has nothing to show. Real dislocations persist,
# and a persistent move stays unusual at every window length.
#
# The build-up is deliberately milder per day than the scenario day itself. For a
# one-session gap those days sit in the baseline the shock is judged against, and a
# build-up as violent as the shock would raise the bar enough to mask it.
RUNUP = 4
MARKET_SHOCK_RUNUP = -0.011
SECTOR_SHOCK_RUNUP = -0.010
ANOMALY_RUNUP = 0.008
# Sitting out a rally is only remarkable if the rally outruns the stock's own noise.
# ZNTH drifts about 1.4% a day on its own, so a rally of two or three percent across a
# week says nothing; the gap only leaves its normal range once the climb is decisive.
RALLY_RUNUP = 0.013

# The sector fall has to read as sector-specific, so the market is held quiet while it
# happens rather than left to drift into its own move.
SECTOR_SHOCK_MARKET_DAMPING = 0.3


def _runup(scenario_day: int) -> range:
    return range(scenario_day - RUNUP, scenario_day)


def _staged(day: int, scenario_day: int) -> bool:
    return scenario_day - RUNUP <= day <= scenario_day


def trading_days(count: int, last: date) -> list[date]:
    days: list[date] = []
    cursor = last
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def build(seed: int = SEED, last_session: date | None = None) -> dict:
    rng = random.Random(seed)
    sessions = trading_days(SESSIONS, last_session or date(2026, 9, 4))

    market_returns = [rng.gauss(0.0004, MARKET_DAILY_VOL) for _ in range(SESSIONS)]

    # Drift is added to the random draw rather than replacing it, so the build-up still
    # looks like a market and not like four identical sessions.
    for day in _runup(MARKET_SHOCK_DAY):
        market_returns[day] += MARKET_SHOCK_RUNUP
    for day in _runup(RALLY_DAY):
        market_returns[day] += RALLY_RUNUP
    for day in _runup(SECTOR_SHOCK_DAY):
        market_returns[day] *= SECTOR_SHOCK_MARKET_DAMPING
    market_returns[MARKET_SHOCK_DAY] = MARKET_SHOCK
    market_returns[SECTOR_SHOCK_DAY] = 0.001
    market_returns[RALLY_DAY] = RALLY

    sector_excess = {
        code: [rng.gauss(0.0, SECTOR_EXCESS_VOL) for _ in range(SESSIONS)]
        for code in SECTORS
    }
    # Sector dispersion collapses in a broad move: when the whole market is selling,
    # sectors stop going their own way. Leaving it at full width let one sector's draw
    # cancel most of the market's fall for its stocks, which then looked ordinary rather
    # than caught in a crash, and the fall stopped being visibly market-wide.
    for scenario in (MARKET_SHOCK_DAY, RALLY_DAY):
        for day in range(scenario - RUNUP, scenario + 1):
            for code in SECTORS:
                sector_excess[code][day] *= SCENARIO_DAMPING

    for day in _runup(SECTOR_SHOCK_DAY):
        sector_excess[SECTOR_SHOCK[0]][day] += SECTOR_SHOCK_RUNUP
    sector_excess[SECTOR_SHOCK[0]][SECTOR_SHOCK_DAY] = (
        SECTOR_SHOCK[1] - market_returns[SECTOR_SHOCK_DAY]
    )

    indices = {MARKET_INDEX: _series(1000.0, market_returns)}
    for code, excess in sector_excess.items():
        combined = [m + e for m, e in zip(market_returns, excess)]
        indices[code] = _series(1000.0, combined)

    symbols, prices = {}, {}
    for spec in SYMBOLS:
        reference = (
            [m + e for m, e in zip(market_returns, sector_excess[spec.sector_index])]
            if spec.sector_index
            else market_returns
        )
        idio = [
            rng.gauss(0.0, spec.idio_vol * _idio_damping(spec, day))
            for day in range(SESSIONS)
        ]
        returns = [r + i for r, i in zip(reference, idio)]

        if spec.symbol == ANOMALY[0]:
            for day in _runup(ANOMALY_DAY):
                returns[day] += ANOMALY_RUNUP
            returns[ANOMALY_DAY] = reference[ANOMALY_DAY] + ANOMALY[1]
        if spec.symbol == RALLY_NON_PARTICIPANT:
            # Sitting out a rally means going nowhere while the market climbs, not
            # printing an identical zero for five sessions.
            for day in _runup(RALLY_DAY):
                returns[day] = idio[day] * 0.25
            returns[RALLY_DAY] = 0.0

        series = _apply_actions(_series(spec.base_price, returns), spec.symbol, sessions)
        start = spec.listed_after
        symbols[spec.symbol] = {
            "symbol": spec.symbol,
            "name": spec.name,
            "sector_index": spec.sector_index,
            "listed_on": sessions[start].isoformat(),
        }
        prices[spec.symbol] = [
            {
                "date": sessions[i].isoformat(),
                "close": round(series[i], 2),
                "volume": _volume(rng, spec.symbol, i),
            }
            for i in range(start, SESSIONS)
        ]

    return {
        "meta": {
            "synthetic": True,
            "seed": seed,
            "sessions": SESSIONS,
            "generated_for": "Reckon demo and tests",
            "notice": (
                "Fictional issuers and generated prices. Not real market data "
                "and not derived from any exchange feed."
            ),
            "scenarios": {
                sessions[day].isoformat(): label for day, label in SCENARIOS.items()
            },
        },
        "sessions": [d.isoformat() for d in sessions],
        "sector_names": SECTORS,
        "market_index": MARKET_INDEX,
        "indices": {
            code: [
                {"date": sessions[i].isoformat(), "close": round(values[i], 2)}
                for i in range(SESSIONS)
            ]
            for code, values in indices.items()
        },
        "symbols": symbols,
        "prices": prices,
        "events": _events(sessions),
    }


def _apply_actions(series: list[float], symbol: str, sessions: list[date]) -> list[float]:
    """Make the price series reflect the corporate actions the fixture declares.

    A fixture that announces a 1:2 split without ever halving the price is
    internally inconsistent, and the classifier's adjustment then produces a
    wildly wrong return. Applying the action here means the test suite verifies
    a genuine round trip: the generator halves the price, the adjustment
    restores the anchor, and the reported change is near zero.
    """
    by_date = {date.fromisoformat(e["on_date"]): e for e in _events(sessions) if e["symbol"] == symbol}
    if not by_date:
        return series

    adjusted = list(series)
    for index, day in enumerate(sessions):
        event = by_date.get(day)
        if event is None or index == 0:
            continue
        if event["kind"] == "SPLIT":
            factor = 1.0 / event["value"]
        elif event["kind"] == "EX_DIVIDEND":
            previous = adjusted[index - 1]
            factor = (previous - event["value"]) / previous
        else:
            continue
        for later in range(index, len(adjusted)):
            adjusted[later] *= factor

    return adjusted


SCENARIO_DAMPING = 0.2


def _idio_damping(spec: SymbolSpec, day: int) -> float:
    """Suppress idiosyncratic noise on the days a scenario is being staged.

    A scenario left to chance is not a scenario. At full idiosyncratic
    volatility a single unlucky draw can leave a stock down 1.5% on a day its
    sector fell 4%, which is a perfectly reasonable market outcome but destroys
    the case the fixture exists to demonstrate.
    """
    if _staged(day, MARKET_SHOCK_DAY) or _staged(day, RALLY_DAY):
        return SCENARIO_DAMPING
    if _staged(day, SECTOR_SHOCK_DAY) and spec.sector_index == SECTOR_SHOCK[0]:
        return SCENARIO_DAMPING
    if _staged(day, ANOMALY_DAY) and spec.symbol != ANOMALY[0]:
        return SCENARIO_DAMPING
    return 1.0


def _series(start: float, returns: list[float]) -> list[float]:
    values, level = [], start
    for r in returns:
        level *= 1.0 + r
        values.append(level)
    return values


def _volume(rng: random.Random, symbol: str, index: int) -> int:
    # Python salts str.__hash__ per process, so a stable digest is needed for
    # the fixture to be byte-identical across runs.
    base = 300_000 + (int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 400_000)
    multiplier = rng.uniform(0.7, 1.4)
    if symbol == ANOMALY[0] and index == ANOMALY_DAY:
        multiplier = 3.6
    return int(base * multiplier)


def _events(sessions: list[date]) -> list[dict]:
    """Four events, each of a kind we can source reliably and date exactly."""
    return [
        {
            "symbol": "MRDB",
            "on_date": sessions[SECTOR_SHOCK_DAY].isoformat(),
            "kind": "SPLIT",
            "value": 2.0,
            "detail": "1:2 stock split",
        },
        {
            "symbol": "DCNP",
            "on_date": sessions[TODAY - 2].isoformat(),
            "kind": "EX_DIVIDEND",
            "value": 14.0,
            "detail": "Final dividend of Rs 14 per share",
        },
        {
            "symbol": "KVRB",
            "on_date": sessions[MARKET_SHOCK_DAY].isoformat(),
            "kind": "RESULTS",
            "value": None,
            "detail": "Quarterly results announced",
        },
        {
            "symbol": "SETL",
            "on_date": (sessions[TODAY] + timedelta(days=3)).isoformat(),
            "kind": "RESULTS",
            "value": None,
            "detail": "Board meeting to consider quarterly results",
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="fixtures/market.json")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    fixture = build(seed=args.seed)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The fixture is committed, so its bytes are part of the repo. Without an explicit
    # newline and encoding, Windows writes CRLF in cp1252 and the same seed produces a
    # different file than Linux, breaking the byte-identical guarantee the tests rely on.
    path.write_text(json.dumps(fixture, indent=2), encoding="utf-8", newline="\n")

    print(f"wrote {path}")
    print(f"  {len(fixture['symbols'])} symbols, {len(fixture['indices'])} indices, {SESSIONS} sessions")
    for day, label in fixture["meta"]["scenarios"].items():
        print(f"  {day}  {label}")


if __name__ == "__main__":
    main()
