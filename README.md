# Reckon

**What did you miss while you were away?**

Reckon checks every stock on your watchlist and tells you which ones deserve
your attention — and, just as importantly, why the rest didn't.

A normal watchlist app shows you a filtered list and leaves you wondering what
it dropped. Reckon accounts for every symbol it was given, every time. Sixteen
watched, sixteen explained: one needs you, twelve moved with the market, two
were quiet, one could not be judged at all. Nothing is silently omitted, and the
reasoning behind every decision — including the decisions *not* to flag
something — is kept and shown.

---

## The idea

Every watched symbol gets exactly one verdict:

| Verdict | Meaning |
| --- | --- |
| `EVENT` | A corporate action is on record for this window. A fact. |
| `UNEXPLAINED` | An unusual move that neither the market nor the sector accounts for. |
| `WITH_MARKET` | Moved consistently with the market index. |
| `WITH_SECTOR` | Moved consistently with its sector index. |
| `QUIET` | Within its own normal range, and close to the market. |
| `CANT_SAY` | The data does not support a claim. |

And one epistemic label: `KNOWN` (a sourced fact), `INFERRED` (a comparison
against a baseline), `UNKNOWN` (an explicit absence of evidence).

Three commitments hold the product together:

**No fixed thresholds.** "Flag anything beyond 2%" cannot be defended — 2% is a
large move for one stock and an ordinary day for another. A move is instead
ranked against *the same stock's own history of the same quantity*, expressed as
a percentile: *this move is larger than 90% of what this stock normally does
over this many sessions.*

**No causation from correlation.** A percentile comparison says two things moved
together. It never says one moved the other. Every user-facing sentence lives in
[`app/api/copy.py`](app/api/copy.py) and is asserted against a banned-word list
(`because`, `caused`, `due`, `should`, `buy`, `sell`, …) by the test suite, so a
template that drifts into causal or advisory language fails CI rather than
shipping.

**Doubt outranks confidence.** A stale price, too little history, or a corporate
action that cannot be adjusted produces `CANT_SAY`, not a confident guess.

---

## Running it

```bash
pip install -r requirements.txt
(cd web && npm install && npm run build)
python -m uvicorn app.api.main:app --reload
```

Then open <http://localhost:8000>. The database (SQLite by default) is created,
migrated and seeded with the sample fixture on first start; no other service is
needed. Point `DATABASE_URL` at Postgres for a real deployment.

### Tests

```bash
python -m pytest
```

No test reaches the network or needs a token.

---

## Where the data comes from

Reckon is one product with two data sources and one showcase.

- **Live data** — real NSE listings and prices through Yahoo Finance. Search
  resolves tickers *and* company names; adding a symbol fetches its history, the
  Nifty 50, its sector index where one is reliably published, and its corporate
  actions.
- **Sample data** — sixteen prepared issuers on a fixed calendar. The same
  classifier and the same brief, with results that never change between runs.
- **Demo** — a separate guest session with a preloaded watchlist and four
  designed scenarios (market-wide fall, sector fall, unexplained move, rally with
  one stock flat) plus a stale-data switch. It is a different user on the server,
  so nothing done in the demo can touch a real watchlist or anchor.

Live and Sample are properties of *your watchlist*, chosen inside the Watchlist
screen. Brief and Watchlist are the only two places to be.

### Yahoo is a public, on-demand source

Prices are fetched when you ask for them. They are not streamed and Reckon never
describes them as real time. The UI keeps four moments deliberately apart:

- **Last checked** — the previous time you opened Reckon, per data source.
- **Data fetched** — when Reckon last contacted the provider.
- **Analysis through** — the latest *completed* NSE session. Every verdict is
  computed against this, including while the market is open.
- **Latest available quote** — the newest price on record, which may be newer
  than the analysis, and is shown separately for exactly that reason.

A bar for a session still in progress is never stored and never classified. A
sector index that has stopped publishing is dropped rather than allowed to
truncate a stock's history. A failing symbol degrades to `CANT_SAY` and leaves
the rest of the brief intact.

---

## How it is put together

```
app/
  domain/      the classifier, the statistics and the taxonomy.
               Pure: no I/O, no clock, no database.
  sources/     provider.py  the vocabulary a live provider produces
               yahoo.py     the live adapter
               live.py      the only writer of live market data
               replay.py    turns stored rows back into classifier inputs
               nse.py       regular exchange hours, for wording only
  services/    briefing, watchlist, visits, simulation, identity
  api/         routes, serialisation, and copy.py (all user-facing wording)
  db/          models, migrations bridge, fixture seeding
web/src/       the frontend: Brief, Detail, Watchlist, Welcome
```

Live data is not a second pipeline. It writes into the same tables the fixture
writes, so the reader, the classifier, the brief and the UI above it are the code
that already existed — there is one classifier, and one way a verdict is reached.

Rows are tagged by source and the session calendar is keyed by source, so live
data can never move the moment a demo brief is computed for.

### Three clocks, kept apart

| Clock | Moves when | Means |
| --- | --- | --- |
| `users.last_open_at` | any brief is read | you opened Reckon |
| `user_source_visit.last_open_at` | a brief is read, after a real absence | "you last checked", per source |
| `user_symbol_anchor.anchor_at` | you acknowledge a snapshot | the comparison point |

Acknowledgement carries the id of the snapshot you were actually shown. If a
newer price arrived between render and tap, the guarded `UPDATE` matches nothing
and the newer change stays unread — rather than being marked seen by a tap that
never saw it.

---

## What the brief shows

**Start here** — the single strongest item worth opening, taken from the head of
Needs You rather than from a second scoring system.

**Needs you** — `EVENT` and `UNEXPLAINED`.

**Explained** — `WITH_MARKET` and `WITH_SECTOR`, compressed to one line per
*explanation* rather than one card per ticker, expandable to the stocks.

**Quiet** · **Couldn't confidently evaluate** — the latter grouped by stated
reason: an untrusted price, insufficient history, an unadjustable corporate
action.

**What Reckon checked** — the silence report. *"16 stocks checked. 12 moved with
the market or its sector, 2 were quiet, 1 could not be evaluated and 1 needs your
attention."* Real counts, from the same numbers the sections are built from.

Open any symbol — flagged or not — and the detail view replays the classifier's
own precedence as a decision trace: data quality → event check → own movement →
market comparison → sector comparison → final verdict, with the skipped steps
still visible, because stopping early is the whole point of the order.
