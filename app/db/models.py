"""Persistent schema.

Three guarantees live in this file's keys and are relied on by the services:

  1. Adding a watched symbol twice is one row      -- PK(user_id, symbol)
  2. A late quote never overwrites a newer one     -- guarded UPDATE on quotes
  3. An anchor never moves backwards               -- guarded UPDATE on anchors

Two clocks are stored separately and deliberately. `users.last_open_at` is
narrative ("you last looked on Tuesday") and moves whenever a brief is read.
`user_symbol_anchor.anchor_at` is the comparison point and moves only when the
user acknowledges a specific snapshot. Collapsing them would mark a symbol read
merely because the app was opened.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_open_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Which market data this user's watchlist is read from. Demo and live
    # sessions are different users, so this keeps their data apart.
    data_source: Mapped[str] = mapped_column(String(8), default="replay")

    # Reserved for the credentialed sign-in the plan defers. Guest sessions need
    # no credentials, so both stay null rather than holding an empty password.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SessionToken(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Symbol(Base):
    __tablename__ = "symbols"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    sector_index: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    listed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(8), default="replay", index=True)
    # The provider's own identifier, needed to fetch anything about a live
    # instrument. Null for fixture symbols, which no provider knows about.
    instrument_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(
        String(24), ForeignKey("symbols.symbol"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class TradingDay(Base):
    """The session calendar, kept per source.

    Part of the primary key rather than a plain column: the fixture's synthetic
    calendar and the exchange's real one overlap on most weekdays, and a shared
    calendar would let live data move the moment a demo brief is computed for.
    """

    __tablename__ = "trading_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(8), primary_key=True, default="replay")
    close_at: Mapped[datetime] = mapped_column(DateTime)


class DailyBar(Base):
    __tablename__ = "daily_bars"

    symbol: Mapped[str] = mapped_column(
        String(24), ForeignKey("symbols.symbol"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="replay")


class IndexBar(Base):
    __tablename__ = "index_bars"

    index_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Float)


class IndexMeta(Base):
    """Names each index and marks which one is the market.

    Recorded rather than inferred: deriving the market index from whichever
    codes no symbol claims as its sector breaks the first time a sector index
    is seeded before any symbol references it.
    """

    __tablename__ = "indices"

    index_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    is_market: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(8), default="replay", index=True)
    instrument_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CorporateEventRow(Base):
    __tablename__ = "corporate_events"
    __table_args__ = (
        UniqueConstraint("symbol", "occurred_on", "kind", name="uq_event_identity"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(
        String(24), ForeignKey("symbols.symbol"), index=True
    )
    occurred_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="replay")


class QuoteRow(Base):
    __tablename__ = "quotes"

    symbol: Mapped[str] = mapped_column(
        String(24), ForeignKey("symbols.symbol"), primary_key=True
    )
    price: Mapped[float] = mapped_column(Float)
    event_time: Mapped[datetime] = mapped_column(DateTime)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(16), default="replay")
    freshness: Mapped[str] = mapped_column(String(16), default="CLOSED")
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)


class UserSymbolAnchor(Base):
    __tablename__ = "user_symbol_anchor"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(
        String(24), ForeignKey("symbols.symbol"), primary_key=True
    )
    anchor_at: Mapped[datetime] = mapped_column(DateTime)
    anchor_price: Mapped[float] = mapped_column(Float)
    anchor_adj: Mapped[float] = mapped_column(Float, default=1.0)
    anchor_snapshot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class SimulationState(Base):
    """A demo override, scoped to one user and removable in one call.

    Held per user rather than globally so a judge driving the demo cannot alter
    what anyone else sees, and stored rather than held in memory so it survives
    the page reloads a walkthrough involves.

    An absent row is the normal state. Nothing reads these columns unless a row
    exists, which is what keeps the production path untouched.
    """

    __tablename__ = "simulation_state"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    anchor_sessions_ago: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"SYMBOL": "STALE"} or {"*": "DISPUTED"} to cover the whole watchlist.
    freshness_json: Mapped[str] = mapped_column(Text, default="{}")
    scenario: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VerdictRow(Base):
    """One classification, as it was shown to one user.

    Retained for two reasons: an acknowledgement names a snapshot and the server
    has to resolve it back to the price and time actually on screen, and the
    detail view has to answer "why did you not flag this?" from the evidence
    that produced the verdict rather than by recomputing a different one.
    """

    __tablename__ = "verdicts"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "snapshot_id", name="uq_verdict_snapshot"),
        Index("ix_verdicts_user_symbol", "user_id", "symbol"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE")
    )
    symbol: Mapped[str] = mapped_column(String(24), ForeignKey("symbols.symbol"))
    computed_for_anchor: Mapped[datetime] = mapped_column(DateTime)
    verdict: Mapped[str] = mapped_column(String(16))
    epistemic: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(32))
    evidence_json: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[str] = mapped_column(String(32), index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime)
    snapshot_price: Mapped[float] = mapped_column(Float)
    snapshot_adj: Mapped[float] = mapped_column(Float, default=1.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
