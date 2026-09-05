# Reckon

Reckon is a returning-user intelligence layer for stock watchlists. Instead of making users scan raw price movements, it determines what changed since their last check, what matters, what is explainable, and what can be safely ignored.

---

## THE CORE IDEA

Every watched stock receives exactly one clear outcome:

| Verdict       | Description                                                                                        |
| ------------- | -------------------------------------------------------------------------------------------------- |
| **Needs You** | Unusually large movement relative to its history, or a confirmed event affecting the stock         |
| **Explained** | Significant movement that closely follows the broader market or sector                             |
| **Quiet**     | Movement remains within its normal historical range with no significant market or sector deviation |
| **Can’t Say** | Data is stale, disputed, insufficient, or otherwise unreliable                                     |

This means every stock is accounted for. Silence is a deliberate result, not a missed alert.

---

## WHY RECKON IS DIFFERENT

### 1. Attention Compression

Reckon avoids alert spam. If 12 stocks move with the market, they can be represented as one meaningful explanation instead of 12 separate alerts.

### 2. Decision Traces

Every important verdict is auditable. The Decision Trace shows how Reckon evaluated data quality, events, stock movement, market context, and sector context before reaching its conclusion.

### 3. Known, Inferred, Unknown

Reckon separates verified facts from statistical inference. When the available evidence does not establish a cause, it explicitly marks the reason as unknown rather than inventing one.

### 4. Returning-User Memory

Reckon remembers the user's previous check and maintains a stock-level acknowledgement snapshot. A new signal cannot be hidden by an acknowledgement of an older snapshot.

### 5. Uncertainty Mode

When confidence in the underlying data decreases, Reckon makes fewer claims instead of becoming more speculative.

---

## THE INTELLIGENCE LAYER

The classification engine compares a stock against:

1. Its own historical behaviour
2. Overall market movement
3. Sector movement

It uses rolling historical windows and a 90th-percentile threshold to identify unusually large movements. Confirmed events and data quality are evaluated before the final verdict.

Reckon also distinguishes an in-progress trading session from the latest completed session and accounts for corporate actions such as stock splits, preventing misleading conclusions from raw price data.

---

## MODES

### 1. Live+Sample

Live allows users to search and add real NSE stocks using on-demand Yahoo Finance data. The Brief exposes data freshness and the completed market session used for analysis. Live data requires an internet connection.

Sample provides predictable preloaded market data for exploring the same workflow without depending on live market conditions.

### 2. Demo

Demo is a curated, deterministic showcase of Reckon's reasoning. It covers market-wide movements, sector movements, isolated anomalies, rallies, and unavailable or unreliable data.

This makes the product's core intelligence immediately demonstrable without requiring users to configure a watchlist first.

---

## TECHNICAL ARCHITECTURE

Reckon uses a React/Vite frontend, FastAPI backend, PostgreSQL persistence, and Alembic database migrations.

The core classification logic is isolated as a deterministic domain layer, independent of network and database I/O, making the most important reasoning independently testable.

The application is packaged as a Dockerized single service and maintains source isolation between Live, Sample, and Demo experiences.

---

## Live Application

https://reckon-gpoo.onrender.com/
