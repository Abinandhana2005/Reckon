# Reckon — Project Context

This document describes what the Reckon codebase **actually implements today**, as of the current repository state. It is written to be pasted into a fresh LLM/coding-agent session with zero prior context.

---

## 1. Product purpose + problem statement

Reckon is a stock watchlist app whose core promise is **exhaustive accounting, not filtering**. A normal watchlist/alerting app shows a filtered subset of "interesting" moves and leaves the reader wondering what was silently dropped. Reckon instead assigns **exactly one verdict to every symbol on a user's watchlist, every time**, drawn from a small, mutually exclusive and collectively exhaustive taxonomy, and shows its reasoning — including *why* a symbol was **not** flagged. Nothing is ever omitted from the accounting; the UI's "silence report" states counts that must sum to the total watched.

Three product commitments enforced in code:
- **No fixed thresholds.** A move is ranked as a percentile against *that stock's own historical distribution* of the same-length move, never against a flat "±2%" rule.
- **No causation from correlation.** All user-facing copy lives in one file (`app/api/copy.py`) and is asserted against a banned-word list (`because`, `caused`, `due`, `should`, `buy`, `sell`, `undervalued`, `bullish`, …) by the test suite. A percentile comparison is phrased as "consistent with", never as an explanation.
- **Doubt outranks confidence.** Stale/disputed/unavailable prices, insufficient history, or an unadjustable corporate action all produce `CANT_SAY`, never a confident guess.

---

## 2. User flow / key screens

Entry is `web/src/App.jsx`. There is no traditional router library — `web/src/router.js` is a ~25-line `pushState`/`popstate` hook (`usePath`, `link`), and the backend serves `index.html` for any unrouted non-`/api` path so refresh/back/forward/deep-links work.

- **Welcome** (`screens/Welcome.jsx`) — shown only when the real user's watchlist is empty. Two-step: (1) "Try the demo" vs "Build my watchlist"; (2) if building, choose **Live data** (disabled if unavailable, with the reason shown) or **Sample data**.
- **Brief** (`screens/Brief.jsx`, route `/`) — the default landing screen once onboarded. Shows a provenance "band" (4 timestamps, see §9), a hero line ("3 of 16 need you"), an accounting bar (proportional counts), "Start here" (single top-priority card), "Needs you" (EVENT/UNEXPLAINED cards), "Explained" (collapsed one-line-per-sector/market groups, expandable to member stocks), "Quiet" (chips), "Couldn't confidently evaluate" (grouped by reason), and a closing "What Reckon checked" silence-report ledger.
- **Detail** (`screens/Detail.jsx`, route `/s/:symbol`) — one symbol's full evidence: headline/prose, "why (not) flagged", a 6-step decision trace (see §7) with skipped steps still shown, a sparkline with the anchor marked, supporting numbers, freshness rows, and an acknowledge ("Mark as seen") action.
- **Watchlist** (`screens/Watchlist.jsx`, route `/watchlist`) — data-source switch (Live/Sample), symbol search (debounced provider search in live mode; in-browser filter of 16 fixture issuers in sample mode), add/remove.
- **Demo** — not a route. It is a second guest session (separate token) with a `DemoBar` band always visible, offering 4 designed scenarios and a "stale data" toggle. Leaving the demo just switches back to the real session's token.

---

## 3. Backend + frontend architecture

**Backend**: FastAPI, one process, no background workers/scheduler (`app/api/main.py`). SQLAlchemy 2.0 ORM. SQLite by default; Postgres via `DATABASE_URL` (auto-normalized to the `psycopg` v3 driver — see §12). Alembic migrations run automatically at startup.

Layout:
```
app/
  domain/    classifier.py, verdicts.py (taxonomy/types), statistics.py (percentile baselines),
             adjustments.py (corporate-action math), brief.py (grouping). Pure: no I/O, no clock.
  sources/   provider.py (shared vocabulary: Bar/Instrument/LiveQuote/ProviderError hierarchy,
             static sector maps), yahoo.py (the only live adapter), live.py (the only writer of
             live data into the shared tables), replay.py (turns stored rows into classifier
             inputs — used by BOTH sample and live), nse.py (exchange hours, wording only).
  services/  briefing.py (assembles the brief, moves the two clocks), watchlist.py (membership +
             anchors), visits.py ("last checked" clock), simulation.py (demo overrides),
             identity.py (guest sessions).
  api/       routes/ (session, watchlist, briefing, live, dev), serialize.py, copy.py (all
             user-facing wording), deps.py (current_user/optional_user), main.py (app assembly).
  db/        models.py, base.py (engine/session), migrate.py (startup migration bridge), seed.py.
web/src/     React 18 + Vite SPA: App.jsx, api.js (fetch client), router.js, screens/, components/.
```

**Frontend**: React 18, no state-management library (plain `useState`/`useEffect` in `App.jsx`), no CSS framework (hand-written `styles.css`). Built with Vite to `web/dist`, served by FastAPI itself from the same origin (`StaticFiles` + a catch-all SPA route registered after the API routes). **No CORS middleware exists** — same-origin serving is the only supported topology today.

Live data is explicitly **not a second pipeline**: `app/sources/live.py` writes into the exact same tables (`daily_bars`, `index_bars`, `quotes`, `corporate_events`, `symbols`, `trading_days`) that the sample fixture seeds. Everything above that layer — `replay.py`'s readers, the classifier, the brief assembly, the API, the UI — is identical code for both sources.

---

## 4. Data model and session/state persistence

All product state lives server-side, keyed by an opaque guest `user_id`. The browser holds only session tokens (`localStorage`, two keys: `reckon.session` real, `reckon.demo.session` demo) plus a `reckon.mode` flag and a theme preference — **no cookies**, no other client-side state. Every request carries `X-Session-Token`.

Tables (`app/db/models.py`):
- **`users`**: `id` (uuid hex PK), `created_at`, `last_open_at`, `data_source` (`replay`|`live`), `email`/`password_hash` (nullable, reserved for a credentialed sign-in that is not implemented — every session today is a guest).
- **`sessions`** (`SessionToken`): `token` PK, `user_id` FK, `created_at`, `expires_at` (30-day TTL, `SESSION_TTL_DAYS`).
- **`user_source_visit`**: composite PK `(user_id, source)`, `last_open_at` — the per-source "last checked" clock (see §8).
- **`symbols`**: `symbol` PK, `name`, `sector_index`, `isin`, `status`, `listed_on`, `source` (`replay`|`live`), `instrument_key` (provider's own id; null for fixture symbols). One shared catalogue table; a symbol code belongs to exactly one source (enforced in application code, not a DB constraint).
- **`watchlist_items`**: composite PK `(user_id, symbol)`, `added_at`, `note`, `sort_order`.
- **`trading_days`**: composite PK `(day, source)`, `close_at` — the session calendar, kept **separately per source** so a live fetch can never move the moment a sample/demo brief is computed for.
- **`daily_bars`**: composite PK `(symbol, day)`, `close`, `volume`, `source`.
- **`index_bars`**: composite PK `(index_code, day)`, `close`.
- **`indices`** (`IndexMeta`): `index_code` PK, `name`, `is_market`, `source`, `instrument_key`.
- **`corporate_events`**: uuid PK, `symbol` FK, `occurred_on`, `kind`, `value`, `detail`, `source`; unique `(symbol, occurred_on, kind)`.
- **`quotes`** (`QuoteRow`): `symbol` PK (one row per symbol), `price`, `event_time`, `ingested_at`, `source`, `freshness`, `prev_close`. Writes are guarded (`event_time <= new_event_time`) so a late/out-of-order refresh can never rewind a newer price.
- **`user_symbol_anchor`**: composite PK `(user_id, symbol)`, `anchor_at`, `anchor_price`, `anchor_adj`, `anchor_snapshot_id` — the comparison reference point (see §8).
- **`simulation_state`**: `user_id` PK, `as_of`, `anchor_sessions_ago`, `freshness_json`, `scenario`, `updated_at` — demo overrides, one row per user, absent row = no override.
- **`verdicts`** (`VerdictRow`): uuid PK, `user_id`, `symbol`, `computed_for_anchor`, `verdict`, `epistemic`, `reason`, `evidence_json`, `snapshot_id` (unique with `user_id`+`symbol`), `snapshot_at`, `snapshot_price`, `snapshot_adj`, `computed_at` — retains every verdict a user was actually shown, keyed by the snapshot it was computed for; this is what acknowledgement and "why wasn't this flagged?" read back from.

Migrations: 4 Alembic revisions (`b1a573c914fc` initial → `a8c5a409897f` simulation_state → `0f3de7537fad` live_mode_data_source → `c4a1d9e02b17` per_source_visit_clock, current head). Startup (`app/db/migrate.py: ensure_schema`) runs `alembic upgrade head` on a fresh DB, or infers and stamps the correct historical revision for a pre-existing unstamped DB before upgrading, and clears orphaned SQLite batch-migration scratch tables from an interrupted run.

---

## 5. Live/Sample/Demo modes and their isolation

- **Sample** (`SOURCE_REPLAY = "replay"`): 16 deterministic fictional issuers on a fixed synthetic calendar, loaded once from `fixtures/market.json` at startup if the DB is empty (`app/db/seed.py`). Never changes between runs.
- **Live** (`SOURCE_LIVE = "live"`): real NSE data fetched on demand through Yahoo Finance. Never streamed; never described as real-time anywhere in the code or copy.
- **Demo**: **not a third data source.** It is a second guest user (separate `user_id`, separate session token) that runs entirely on Sample data with an active `SimulationState` override (pinned `as_of` date matching one of 4 fixture-designed scenarios, optional injected stale/disputed/unavailable freshness). Its watchlist, anchors, and verdict history are ordinary rows under a different `user_id` — isolation is structural (foreign keys to that user), not a special code path.

Isolation mechanisms:
- `data_source` on `users` picks which source a user's brief/watchlist currently reads; `POST /api/mode` switches it. Each source is a **completely separate watchlist for the same user** — switching neither merges nor deletes anything.
- `Symbol.source`, `TradingDay.source`, `DailyBar.source`, `IndexMeta.source` tag every market-data row; a symbol/day/bar can never be read across sources. Adding a symbol that already exists under the *other* source is refused outright (`LiveDataUnavailable` / `UnknownSymbol`) rather than silently reused.
- `trading_days` (the session calendar) is keyed per source, so switching or refreshing one source's data can never change what "now"/"latest session" means for the other.
- A demo brief and a real (or another demo) user's brief never share rows beyond the read-only reference market data itself — verified by dedicated tests (`test_a_demo_brief_is_unchanged_by_live_data`, `test_the_demo_session_stays_on_fixture_data_while_another_goes_live`).

---

## 6. Yahoo data flow — exactly what is fetched/stored

Adapter: `app/sources/yahoo.py`, wrapping `yfinance` (optionally through `curl_cffi` with a browser-profile session/User-Agent to reduce Yahoo's rate-limiting of the plain default identity — still the public, credential-free API, no auth added).

**Search** (`YahooClient.search`): tries the raw query first (appending `.NS` up front breaks company-name search against Yahoo's endpoint), then a `.NS`-suffixed retry for ambiguous bare tickers, then a cross-listing fallback: if only Bombay (`.BO`) listings come back, up to 4 are individually re-looked-up as `SYMBOL.NS` and kept only if Yahoo itself confirms the NSE listing. Results are filtered to `.NS` equities (`quoteType` EQUITY/STOCK/COMMON STOCK), deduped, an exact ticker match is hoisted to the front, and everything else keeps Yahoo's relevance order.

**Adding an instrument** (`live.add_instrument`, retried once on a primary-key race — see §14):
1. `client().find(symbol)` → `Instrument(symbol, name, instrument_key="XXX.NS", isin=None, sector_index)`. `isin` is always `None` (Yahoo doesn't expose it here). `sector_index` comes from a static hand-maintained map (`SYMBOL_SECTORS` in `provider.py`, ~50 well-known NSE large caps across 7 sector indices) — never guessed.
2. Fetches the market index (`^NSEI`, "Nifty 50") daily candles for a window of `LIVE_HISTORY_SESSIONS` (default 220) trading sessions × a 1.6 calendar-day multiplier (~352 calendar days).
3. Fetches the stock's own daily candles for the same window.
4. Both are passed through `completed_sessions()`, which drops any bar whose session close time hasn't actually passed yet — an in-progress session's partial bar is never stored.
5. Stock bars are intersected to the days the market index also has.
6. Sector resolution (`_resolve_sector`): the sector index's own fetched bars are used **only if** they reach the same latest session as the stock **and** leave ≥120 (`MIN_SECTOR_SESSIONS`) sessions after intersecting; otherwise the sector is dropped entirely (not truncated) and the symbol is stored with no sector at all — the classifier then reports the comparison as unavailable rather than fabricating one.
7. The `symbols` row is upserted with `source="live"`. If a symbol code already exists as a fixture symbol, the add is refused.
8. Bars are written to `daily_bars` (deduped by day); corporate actions are fetched from the same cached history frame and written to `corporate_events` (deduped by `(date, kind)`).
9. The quote is refreshed after commit.

**Quote** (`YahooClient.quote` / `live.refresh_quote`): built from the last 5 daily bars (`period="5d"`), deliberately **not** `fast_info` (it carries no trade timestamp for NSE tickers, so age could never be established from it). If the latest bar's normal close time (15:30 IST = 10:00 naive-UTC) has already passed, it's a **settled close**; if not, it's a **live in-progress observation** stamped at "now" (never at the future close time — that would mislabel a mid-session price as settled). Freshness: `LIVE` if fresh and mid-session; `CLOSED` if a settled close within the stale window; `STALE` if older than `LIVE_QUOTE_STALE_HOURS` (default 36h); `UNAVAILABLE` if the provider failed or returned no timestamp — in which case it falls back to the last stored daily-bar close but keeps the `UNAVAILABLE` tag so it can't support inference.

**Refresh** (`POST /api/live/refresh` → `live.refresh_watchlist`): tops up the market index and each watched live symbol's bars since their last stored session (bounded by `REFRESH_LOOKBACK_DAYS=30` or the actual gap) before re-polling quotes, so bars never go stale in the middle of the series. One symbol's failure never stops the others; each symbol's outcome is reported independently.

**Corporate actions**: only ex-dividend and stock-split events are sourced from Yahoo's history frame (`Dividends`/`Stock Splits` columns). `BONUS` and `RESULTS` event kinds exist in the taxonomy but nothing in the live path currently populates them — only the sample fixture can carry those.

**Caching**: each `YahooClient` instance caches fetched history frames in-process, keyed by `(ticker, start, end)`, for 120 seconds (`HISTORY_CACHE_SECONDS`) — long enough that one brief build fetches the market index once rather than once per watched symbol, short enough that pressing Refresh reaches Yahoo again.

---

## 7. Core "meaningful change" classifier

`app/domain/classifier.py: classify(ClassificationRequest) -> Classification`. **Pure function** — no I/O, no clock read internally (`now` is an argument), fully unit-testable.

**Inputs**: `SymbolContext` (sessions[], closes[], market_closes[], optional sector_index/sector_closes[], volume_ratio), `Anchor` (at, price, optional snapshot_id), `Quote` (price, event_time, freshness, source), `now`, `events[]` (corporate events).

**Verdict taxonomy** (mutually exclusive, collectively exhaustive): `CANT_SAY`, `EVENT`, `QUIET`, `WITH_MARKET`, `WITH_SECTOR`, `UNEXPLAINED`. **Epistemic** label on every verdict: `KNOWN` (sourced fact), `INFERRED` (statistical comparison), `UNKNOWN` (explicit absence of evidence).

**Decision precedence** ("a fact outranks a comparison, a comparison outranks a statistic, doubt about the data outranks all of them"):
1. **Freshness gate**: if `quote.freshness` is `STALE`/`DISPUTED`/`UNAVAILABLE` → `CANT_SAY` / `UNTRUSTED_QUOTE`, immediately.
2. **History gate**: the anchor's session must be locatable in the stored series with at least `MIN_HISTORY_BARS = WINDOW_COUNT + 10 - 1 = 69` bars before it → else `CANT_SAY` / `INSUFFICIENT_HISTORY`.
3. **Corporate-action adjustment**: every adjusting event (`SPLIT`, `BONUS`, `EX_DIVIDEND`) inside `(anchor session, latest session]` is folded into one multiplicative `adjustment_factor` (split → `1/ratio`; bonus → `1/(1+ratio)`; ex-dividend → `(close_before_ex − dividend) / close_before_ex`, chained in date order). If any event lacks the data to adjust it (no value, or no prior close on record) → `CANT_SAY` / `UNADJUSTABLE_ACTION` (refuses to show an unadjusted number).
4. **Event check**: any event (adjusting or not) that occurred inside the window → `EVENT` / `KNOWN` / `EVENT_OCCURRED`. Else an event scheduled within the next `UPCOMING_HORIZON_DAYS=5` calendar days → `EVENT` / `KNOWN` / `EVENT_UPCOMING`.
5. **Statistical comparison** (only reached if nothing above decided it): `window = min(max(sessions_away, 1), MAX_GAP_SESSIONS=10)`. Own move, own-vs-market gap, and (if a sector is held) own-vs-sector gap are each ranked as a **percentile** (`percentile_rank`: `100 * count(|history value| < |observed|) / len(history)`) against a baseline of up to `WINDOW_COUNT + window - 1 = 59+window` prior **overlapping** same-length windows, taken strictly before the anchor. `UNUSUAL_PERCENTILE = 90.0`.
   - Both own-percentile `<90` **and** vs-market-percentile `<90` → `QUIET` / `INFERRED` / `WITHIN_OWN_RANGE`.
   - vs-market-percentile `<90` (regardless of own) → `WITH_MARKET` / `INFERRED` / `TRACKED_MARKET`.
   - Else, if a sector is available and vs-sector-percentile `<90` → `WITH_SECTOR` / `INFERRED` / `TRACKED_SECTOR`.
   - Else → `UNEXPLAINED` / `UNKNOWN` / `NO_EXPLANATION_FOUND`.

**Evidence** is retained in full regardless of verdict (`Evidence` dataclass: `sessions_away`, `adjusted_return`, `market_return`, `sector_return`, `adjustment_factor`, `own`/`vs_market`/`vs_sector` `Comparison` objects each carrying `observed`/`percentile`/`typical_abs` [median absolute historical move, display-only]/`sample_size`, `sector_index`, `sector_available`, `events_in_window`/`events_upcoming`, `volume_ratio`, `quote_age_seconds`, `quote_source`, `history_bars`). This is what powers "why wasn't this flagged?" (`copy.why_not_flagged`) and the Detail screen's 6-step decision trace (`copy.decision_trace`), which is **replayed from stored evidence**, not recomputed.

**Snapshot id**: `sha256(f"{symbol}|{quote.event_time.isoformat()}|{quote.price:.4f}")[:16]` — identifies exactly what a user was shown; acknowledgement can only ever advance an anchor to a snapshot that was actually computed for them.

Grouping into brief sections (`app/domain/brief.py`, structural only, no wording): `needs_you` = `EVENT ∪ UNEXPLAINED`; `explained` = `WITH_MARKET ∪ WITH_SECTOR` (compressed to one line per sector/market group, expandable to member stocks); `quiet`; `cant_say`. `Brief.accounted_for` must equal the number of symbols classified — enforced by `briefing.build_brief` raising `BriefAccountingError` if not.

---

## 8. "Since last checked" / anchor semantics

Three deliberately separate clocks (documented at the top of `app/db/models.py`):

- **`users.last_open_at`** — the last time this person opened Reckon **at all**, across every source.
- **`user_source_visit.last_open_at`** (per `(user_id, source)`) — the last time they opened Reckon **on this specific data source**; this is what the brief's "You last checked" narrates. Read **before** a new brief is built (so the header describes the span since the *previous* read), and written **only after** a brief is successfully assembled. It moves forward only when the gap since the stored value exceeds `VISIT_IDLE_MINUTES=30` — re-reading the brief within 30 minutes does not reset "last checked" to "a moment ago". A simulated/demo brief read never advances this clock.
- **`user_symbol_anchor.anchor_at`/`.anchor_price`** (per `(user_id, symbol)`) — the actual comparison reference point the classifier measures against. Moves **only** via explicit acknowledgement (`POST /api/brief/ack`), **only forward** (a guarded SQL `UPDATE ... WHERE anchor_at < row.snapshot_at`), and **only to the exact snapshot** that was shown — never to "now". If a newer snapshot arrived between render and tap, the ack silently no-ops rather than marking an unseen change as read. A newly-watched symbol gets an anchor `DEFAULT_ANCHOR_SESSIONS_AGO=1` session back. Re-adding an already-watched symbol never resets its anchor; removing a symbol keeps the anchor for if it's re-added later.

**Market-hours vs after-close behavior**: for a **Live**-source user, the classifier's `moment` (`as_of`) is **always** `replay.latest_completed_session(db, now, source)` — the latest session whose scheduled close (10:00 naive-UTC / 15:30 IST) is not in the future — **regardless of whether the market is currently open**. So mid-session, verdicts are still computed only against the last fully-closed session; the intraday quote shown alongside (tagged `LIVE`) is not what the comparison is measured from. `classify_symbol` explicitly re-truncates both the quote and the historical context to that same moment for a Live source, so a settled-close-priced stock is never compared against a still-trading market/sector index. For **Sample**/**Demo**, `moment` is the fixture's own last recorded session close (a synthetic, deterministic clock, unrelated to the wall clock) unless a simulation pins `as_of` to a specific scenario date.

---

## 9. Timestamp/freshness semantics

Four moments are deliberately kept apart in both the API payload and the UI, never merged:

1. **"Last checked"** — `user_source_visit.last_open_at`, when the person last opened Reckon on this source.
2. **"Data fetched"** — `max(QuoteRow.ingested_at)` across watched symbols; when Reckon last actually contacted Yahoo.
3. **"Analysis through"** — the latest fully-completed NSE session; every verdict is computed against this, always, even while the market is open.
4. **"Latest available quote"** — `max(QuoteRow.event_time)` among *trusted* quotes (`freshness` in `{LIVE, CLOSED}`); may be newer than "analysis through" (an intraday observation), shown separately for exactly that reason, and never labeled real-time.

`Freshness` enum (`app/domain/verdicts.py`): `LIVE`, `DELAYED` (defined but currently unused by any code path), `STALE`, `CLOSED`, `DISPUTED` (demo-simulation only), `UNAVAILABLE`. `UNTRUSTED_FRESHNESS = {STALE, DISPUTED, UNAVAILABLE}` short-circuits the classifier straight to `CANT_SAY`/`UNTRUSTED_QUOTE` before any statistics run. Freshness is re-derived **at read time**, not by a background job: `live.freshen(quote, now)` recomputes `STALE` if `now − quote.event_time > LIVE_QUOTE_STALE_HOURS` (default 36h) even if the stored row still says `LIVE`/`CLOSED`.

`market_state` (`app/sources/nse.py`) — `OPEN` Mon–Fri 03:45–10:00 naive-UTC (09:15–15:30 IST), else `CLOSED` — is used **only** for a wording field in provenance; it never decides which session is "latest completed" (that comes solely from the actually-observed `trading_days` rows, so an unmodeled holiday can't be miscounted as a session).

---

## 10. Handling of stale, missing, disputed, or insufficient data

- **Stale quote** → `CANT_SAY`/`UNTRUSTED_QUOTE`. Triggered by age (`>36h` by default) or by a provider refresh failure downgrading a previously-trusted stored quote's freshness (`live._store_quote`'s fallback branch: a not-overwritten-but-now-suspect row is re-tagged `STALE`/`UNAVAILABLE`).
- **Disputed quote** → demo-only simulated override (`/api/dev/simulate` can force `STALE`/`DISPUTED`/`UNAVAILABLE` per-symbol or `"*"` for the whole watchlist) → `CANT_SAY`/`UNTRUSTED_QUOTE`.
- **Unavailable quote** → provider raised or returned nothing usable; falls back to the last stored daily-bar close, tagged `UNAVAILABLE` (never invents a fresh price). If *no* price of any age exists, `refresh_quote` raises `LiveDataUnavailable` (surfaces as 404 when adding, or as a per-symbol `"unavailable: ..."` string during a watchlist-wide refresh).
- **Insufficient history** (`<69` bars before the anchor) → `CANT_SAY`/`INSUFFICIENT_HISTORY`.
- **Unadjustable corporate action** (missing value, or an ex-dividend with no recorded close before its ex-date) → `CANT_SAY`/`UNADJUSTABLE_ACTION` — refuses to compute rather than show a number that ignores the action.
- **A whole symbol failing** (unknown to the DB, no market data at all, or a live-fetch error mid-classification) does not fail the whole brief: `briefing.build_brief`'s per-symbol `try/except` turns it into a `CANT_SAY`/`UNTRUSTED_QUOTE`-shaped `Classification` via `_no_data()`, and the rest of the watchlist is still classified normally.
- **Missing sector** (unmapped symbol, or dropped by `_resolve_sector`'s staleness/session-count rule) → reported as `sector_available: false`, never guessed from something else the user happens to watch.
- General rule with no exceptions found in the code: any input a computation needs but doesn't have aborts that computation into `CANT_SAY` rather than substituting a default, zero, or guess.

---

## 11. Main API/service flow

All routes under `/api`, session identified by the `X-Session-Token` header (`app/api/deps.py: current_user`/`optional_user`).

- `POST /api/session` → new guest `User` + `SessionToken` (30-day TTL).
- `GET /api/me` → `user_id`, `last_open_at`.
- `GET/POST /api/mode` → read/switch `user.data_source`; `POST` 503s if `live` is requested but `live_enabled()` is false.
- `GET /api/symbols?q=` → catalogue search scoped to the caller's current source; live mode calls the real Yahoo search (429/502 on provider errors); replay mode does an in-DB `LIKE` search over the 16 fixture symbols.
- `GET /api/watchlist` → membership joined to `Symbol`, filtered to the current source, with names/sector names attached.
- `POST /api/watchlist {symbol, note?, anchor_sessions_ago?}` → `watchlist.add_for_user`: in live mode, fetches+stores a new instrument (or tops up an existing one another user already added) via `live.add_instrument`/`refresh_watchlist`; in replay mode, looks it up in the seeded fixture; then creates the `WatchlistItem` and an anchor (skipped if already watched — note-only update). Cross-source reuse of a symbol code is refused.
- `DELETE /api/watchlist/{symbol}` → removes membership only, source-scoped; anchor and verdict history are retained for a future re-add.
- `GET /api/brief` → `briefing.build_brief`: computes `moment`, classifies every watched symbol (each isolated by `try/except`), records each result as a `VerdictRow`, groups via `domain/brief.assemble`, computes both "market return since last visit" and "market return over the classified window" (two different windows, both shown), advances the visit clock for a genuine (non-simulated) visit, commits, returns the full payload.
- `POST /api/brief/ack {symbol, snapshot_id}` → guarded anchor advance (see §8); 404 if that snapshot was never shown to this user.
- `GET /api/symbols/{symbol}/detail` → 404 if not on the caller's own current-source watchlist; otherwise re-classifies, records the verdict, and returns card + why-not-flagged + full evidence + 60-bar sparkline + decision trace + provenance.
- `POST /api/live/refresh` → 400 if the caller isn't currently in live mode; otherwise `live.refresh_watchlist` over the caller's live watchlist, per-symbol outcome map returned.
- `/api/dev/*` (`GET /scenarios`, `GET/POST/DELETE /simulate`) → demo-only controls, gated by `RECKON_DEV_ENDPOINTS` (on by default), scoped entirely to the caller's own `SimulationState` row.
- Any unrouted `/api/*` path → explicit 404 (not the SPA shell); every other unknown path falls through to `index.html`.

---

## 12. Deployment architecture and required environment variables

One process serves both the API and the built SPA from the same origin (`app.mount`/catch-all route registered after the API routers in `app/api/main.py`) — **no CORS middleware exists**, and none is needed for this topology.

Startup (`lifespan` in `main.py`): `ensure_schema(engine)` (Alembic upgrade, including stamping a pre-existing unstamped DB to the correct historical revision) then `seed(db)` if the DB is empty — a fresh `DATABASE_URL` is fully migrated and seeded automatically, no manual step required.

`Dockerfile`: multi-stage — a `node:20-slim` stage runs `npm ci && npm run build` producing `web/dist`; the final `python:3.12-slim` stage copies the app and that built frontend, runs as a non-root user, and starts `uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}`.

Environment variables (all optional, sensible defaults):
| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local SQLite file | Auto-rewritten: `postgres://`/`postgresql://` → `postgresql+psycopg://` so the installed `psycopg` v3 driver is actually selected |
| `RECKON_FIXTURE` | `fixtures/market.json` | Sample-data fixture path |
| `RECKON_WEB_DIST` | `web/dist` | Built frontend location |
| `RECKON_DEV_ENDPOINTS` | `1` (on) | Set `0`/`false` to disable `/api/dev/*` |
| `RECKON_LIVE_PROVIDER` | `yahoo` | Only `"yahoo"` is currently supported; anything else disables live mode with an "unsupported provider" reason |
| `RECKON_LIVE_HISTORY_SESSIONS` | `220` | Sessions of history fetched per symbol |
| `RECKON_LIVE_QUOTE_STALE_HOURS` | `36` | Quote staleness threshold |
| `RECKON_VISIT_IDLE_MINUTES` | `30` | "Same sitting" window for the visit clock |

**Not currently serverless-ready**: the app relies on process-lifetime global state (a module-global Yahoo client instance and its 120-second in-process history cache) and defaults to a local SQLite file — this fits a persistent-process container host (which the Dockerfile targets) rather than a stateless serverless runtime as-is.

---

## 13. Tests / current verification status

As last verified in this repository:
- Backend: `python -m pytest` → **379 tests pass**, 0 failures. No test reaches the network or needs a token by default — live-mode tests inject a fake provider client via `live.set_client()`; everything runs against an in-memory SQLite database.
- Static check: `python -m pyflakes app` → 0 issues.
- Frontend: `npm run build` (in `web/`) → succeeds cleanly.
- A live network smoke test against the **real** Yahoo API (search → add → history → Brief → Detail → refresh, for `TCS`) was run successfully and confirmed the full pipeline end-to-end.

---

## 14. Important limitations or known caveats

- **Guest-only identity.** No real login exists; a session token *is* the account. Clearing `localStorage` orphans that user's server-side data (nothing is deleted, but nothing recovers access either).
- **`/api/dev/*` is on by default.** Scoped strictly to the caller's own `SimulationState` (no cross-user effect), but it's a live decision to make (`RECKON_DEV_ENDPOINTS=0`) before a production deploy that shouldn't expose demo controls.
- **Yahoo dependency risk.** `yfinance`'s Search/`Ticker.history()` shapes are unofficial and can change without notice; a `curl_cffi` browser-profile session is used only to reduce rate-limiting of the default request identity, not to add authentication.
- **Sector coverage is a small static map** (~50 NSE large caps across 7 sector indices, in `app/sources/provider.py`). Anything outside it gets no sector comparison, by design.
- **Corporate actions from Yahoo cover only ex-dividend and splits.** `BONUS`/`RESULTS` exist in the taxonomy and adjustment math but are never populated from the live path — only the sample fixture can carry them.
- **Concurrency handling is narrow.** A single rollback-and-retry in `live.add_instrument` covers a primary-key race between two users adding the same brand-new live symbol (or the very first live symbol ever, which also creates the shared market-index row); anything beyond that single retry is not further handled.
- **No background jobs.** Every fetch is synchronous and on-demand inside a request; nothing is streamed, pushed, or polled by a scheduler.
- **No rate limiting or abuse protection** beyond the opaque session token on any endpoint.
- **No CORS support.** Splitting frontend and backend across origins is not supported without adding it.

---

## CURRENT SYSTEM IN ONE FLOW

```
User (browser)
  │  clicks / types in the SPA
  ▼
Frontend (React SPA, web/src/App.jsx + api.js)
  │  fetch("/api/...", { headers: { X-Session-Token } })
  ▼
API (FastAPI routes, app/api/routes/*.py)
  │  resolves current_user from the session token
  ▼
Services (app/services/briefing.py, watchlist.py, visits.py, simulation.py)
  │  decide `moment` (latest completed session, or fixture clock, or a pinned
  │  demo scenario), read/advance the visit clock, resolve the user's anchor
  ▼
   ├── DB reads/writes (SQLAlchemy → SQLite/Postgres): symbols, daily_bars,
   │   index_bars, quotes, corporate_events, trading_days, anchors, verdicts
   │
   └── Live source only: app/sources/live.py → app/sources/yahoo.py → yfinance
         → real Yahoo Finance (search / history / quote / corporate actions)
         → normalized and WRITTEN INTO THE SAME DB TABLES above
  │
  ▼
app/sources/replay.py reads those tables back into classifier inputs
  ▼
app/domain/classifier.py: classify() — pure function
  │  freshness gate → history gate → adjustment → event check →
  │  percentile comparisons (own / vs market / vs sector)
  ▼
Verdict + Epistemic + Reason + Evidence  (one of CANT_SAY/EVENT/QUIET/
WITH_MARKET/WITH_SECTOR/UNEXPLAINED)
  │  recorded as a VerdictRow keyed by a snapshot id
  ▼
app/domain/brief.py groups it; app/api/copy.py + serialize.py turn it into
wording and JSON (never causal, never advisory)
  ▼
API response
  ▼
UI (Brief / Detail / Watchlist screens) renders cards, the accounting bar,
the decision trace, and the four separate timestamps — nothing here computes
a price; every number and sentence on screen came from the API response.
```
