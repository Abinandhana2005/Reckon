import React, { useState } from "react";
import {
  SectionHead,
  VerdictRow,
  toneClass,
  toneVar,
  verdictLabel,
} from "../components/ui.jsx";
import {
  movement,
  sessionsClosed,
  shortDate,
  signedPercent,
  stamp,
  visitStamp,
} from "../format.js";

/**
 * The brief.
 *
 * The order is the product's argument, and the reader's attention falls through
 * it: what the data is and how old it is, then how much of the watchlist needs
 * them, then the one thing to open first, then what needs them, what was
 * explained away, what was quiet, what could not be judged, and finally the
 * accounting for all of it.
 *
 * Every value on this page comes from the API. Nothing here is computed from a
 * price: the counts, the sentences and the timestamps are all the server's.
 */

const dash = (value) =>
  value === null || value === undefined ? "—" : signedPercent(value);

/**
 * The provenance band: four moments that answer four different questions.
 *
 * They are never merged. "Data fetched" is when Reckon last spoke to the
 * provider; "Analysis through" is the last completed exchange session, which is
 * what every verdict below was measured against; "Latest available quote" may be
 * newer than the analysis, which is exactly why it sits beside it rather than
 * folded into it. Live prices are fetched on request -- never streamed, and
 * never described as real time.
 */
function bandCells(brief) {
  const provenance = brief.provenance || {};
  const live = brief.data_source === "live";
  const demo = Boolean(brief.simulation && brief.simulation.active);
  // Sample sessions close at a synthetic time that means nothing in any
  // timezone, so they are shown as a date and never as a clock reading.
  const through = live
    ? stamp(provenance.latest_completed_session_at)
    : shortDate(provenance.latest_completed_session_at || brief.as_of);

  if (demo) {
    return [
      { label: "Session", value: "DEMO", note: "Isolated guest session", weight: 600 },
      {
        label: "Data source",
        value: "SAMPLE · DETERMINISTIC",
        note: "Not a real company or price",
        weight: 500,
      },
      {
        label: "Scenario",
        value: (brief.simulation.scenario || "—").toUpperCase(),
        note: "Prepared, repeatable",
        weight: 500,
      },
      { label: "Analysis through", value: through, note: "Latest completed session" },
    ];
  }

  const lastChecked = brief.last_open_at ? visitStamp(brief.last_open_at) : "First visit";

  if (!live) {
    return [
      { label: "Last checked", value: lastChecked, note: "When you last opened Reckon" },
      {
        label: "Data source",
        value: "SAMPLE · DETERMINISTIC",
        note: "A fixed calendar, not the market",
        weight: 600,
      },
      { label: "Analysis through", value: through, note: "Latest completed session" },
    ];
  }

  const quote = provenance.latest_available_quote_at;
  // Only claim the quote is newer when it actually is. Outside a session it is
  // usually the same settled close the analysis ran on, and saying otherwise
  // would invent a freshness the data does not have.
  const quoteIsNewer =
    Boolean(quote) &&
    Boolean(provenance.latest_completed_session_at) &&
    quote > provenance.latest_completed_session_at;
  return [
    { label: "Last checked", value: lastChecked, note: "When you last opened Reckon" },
    {
      label: "Data source",
      value: "LIVE · " + (provenance.provider || "Yahoo Finance").toUpperCase(),
      note: "Fetched on request, not streamed",
      color: "var(--known)",
      weight: 600,
    },
    {
      label: "Data fetched",
      value: stamp(provenance.data_fetched_at),
      note: "Reckon last contacted the provider",
    },
    { label: "Analysis through", value: through, note: "Latest completed NSE session" },
    {
      label: "Latest available quote",
      value: quote ? stamp(quote) : "Unavailable",
      note: quote
        ? quoteIsNewer
          ? "Newer than the analysis; not analysed"
          : "The close the analysis ran on"
        : "No trusted quote on record",
      color: quote ? undefined : "var(--grey)",
    },
  ];
}

function Band({ brief }) {
  return (
    <div className="band">
      <div className="wrap">
        <div className="band-row">
          {bandCells(brief).map((cell) => (
            <div className="band-cell" key={cell.label}>
              <div className="band-label">{cell.label}</div>
              <div
                className="band-value"
                style={{ color: cell.color, fontWeight: cell.weight }}
              >
                {cell.value}
              </div>
              <div className="band-note">{cell.note}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** The five counts, straight from `brief.counts`. */
function countCells(counts) {
  return [
    { key: "checked", n: counts.checked, label: "Checked", color: "var(--ink)" },
    {
      key: "needs_you",
      n: counts.needs_you,
      label: "Need you",
      color: counts.needs_you ? "var(--amber)" : "var(--ink-faint)",
      bar: "var(--amber)",
    },
    {
      key: "explained",
      n: counts.explained,
      label: "Explained",
      color: "var(--ink-soft)",
      bar: "var(--ink)",
    },
    {
      key: "quiet",
      n: counts.quiet,
      label: "Quiet",
      color: "var(--ink-muted)",
      bar: "var(--rule-strong)",
    },
    {
      key: "cant_say",
      n: counts.cant_say,
      label: "Can’t say",
      color: counts.cant_say ? "var(--grey)" : "var(--ink-faint)",
      bar: "var(--grey)",
    },
  ];
}

/**
 * The accounting bar: every watched stock, in proportion.
 *
 * This is the trust signal -- the claim that nothing was filtered out, made
 * visible before the reader has scrolled anywhere.
 */
function Accounting({ counts }) {
  const cells = countCells(counts);
  const total = counts.checked || 0;
  return (
    <div className="accounting">
      <div className="accounting-bar" role="presentation">
        {cells
          .filter((cell) => cell.bar)
          .map((cell) => (
            <div
              key={cell.key}
              style={{
                width: total ? (cell.n / total) * 100 + "%" : 0,
                background: cell.bar,
              }}
            />
          ))}
      </div>
      <div className="accounting-cells">
        {cells.map((cell) => (
          <div className="accounting-cell" key={cell.key}>
            <div className="accounting-n tabular" style={{ color: cell.color }}>
              {cell.n}
            </div>
            <div className="accounting-label">{cell.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * The one item worth opening first.
 *
 * The head of `needs_you`, which the server already orders by the size of the
 * move -- no second scoring model. Its accent follows the same colour rule as
 * every other card: amber only for a move nothing accounts for.
 */
function StartHere({ start, busy, onOpen, onAcknowledge }) {
  if (!start) return null;
  const card = start.card;
  const acking = busy === "ack:" + card.symbol;
  return (
    <section className="starthere">
      <div className="starthere-head">
        <SectionHead title="Start here" gloss="The single thing worth opening first" />
      </div>
      <div className={"starthere-card " + toneClass(card.verdict)}>
        <div className="starthere-figure">
          <div className="starthere-change tabular">{dash(card.change)}</div>
          <div className="starthere-verdict" style={{ color: toneVar(card.verdict) }}>
            {verdictLabel(card)}
          </div>
          <div className="starthere-epistemic">{card.epistemic_label}</div>
        </div>
        <div className="starthere-body">
          <div className="starthere-id">
            <span className="starthere-ticker">{card.symbol}</span>
            <span className="starthere-name">{card.name}</span>
          </div>
          <p className="starthere-headline">{card.headline}</p>
          <p className="starthere-why">{start.line}</p>
          <div className="starthere-actions">
            <button className="btn" onClick={() => onOpen(card.symbol)}>
              See why &rarr;
            </button>
            {onAcknowledge && (
              <button
                className="linkish"
                disabled={acking}
                onClick={() => onAcknowledge(card.symbol, card.snapshot_id)}
              >
                {acking ? "Marking…" : "Mark as seen"}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * One row per explanation, not one card per ticker.
 *
 * The index's own move sits in the gutter and the sentence is the server's. The
 * body expands to the stocks it accounts for, each of which opens its own
 * detail -- because "why wasn't this flagged?" has to be answerable per stock.
 */
function ExplainedGroup({ group, onOpen }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="group">
      <button
        className="group-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="row-gutter">
          <span className="group-move tabular">{dash(group.index_return)}</span>
          <span className="group-kind">
            {group.kind === "MARKET" ? "Market" : "Sector"}
          </span>
        </span>
        <span className="row-body">
          <span className="group-line">{group.line}</span>
          {/* The market group's own sentence already names the index, so the
              sub-line would only repeat it. A sector group's does not. */}
          {group.kind === "SECTOR" && (
            <span className="group-sub">{group.sector_name}</span>
          )}
        </span>
        <span className="group-end">
          <span className="sect-count">{group.size}</span>
          <span className={"chev " + (open ? "open" : "")} aria-hidden="true">
            &#9654;
          </span>
        </span>
      </button>
      {open && (
        <div className="group-body">
          <p className="group-hint">
            Affected stocks &middot; open any to see why it wasn&rsquo;t flagged
          </p>
          {group.symbols.map((card) => (
            <button
              className="member"
              key={card.symbol}
              onClick={() => onOpen(card.symbol)}
            >
              <span className="member-ticker">{card.symbol}</span>
              <span className="member-name">{card.name}</span>
              <span className="member-change tabular">{dash(card.change)}</span>
              <span className="row-arrow" aria-hidden="true">
                &rarr;
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Brief({ brief, busy, onOpen, onViewEverything, onAcknowledge }) {
  const counts = brief.counts;
  const needsYou = brief.needs_you;
  const groups = brief.explained.groups;
  const market = brief.market || {};
  const marketReturn =
    market.return_since_last_open === null || market.return_since_last_open === undefined
      ? market.return_over_window
      : market.return_since_last_open;

  // Built from the counts the server sent, not from any price on this page.
  const heroLine =
    counts.needs_you === 0
      ? "Nothing needs you."
      : counts.needs_you === 1
        ? "One of " + counts.checked + " needs you."
        : counts.needs_you + " of " + counts.checked + " need you.";

  const heroSub = [
    "Here’s what changed.",
    sessionsClosed(brief.sessions_since_last_open),
    marketReturn === null || marketReturn === undefined
      ? null
      : (market.name || "The market") + " " + movement(marketReturn) + ".",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div>
      <Band brief={brief} />
      <div className="wrap page">
        <header className="brief-head">
          <div className="brief-head-main">
            <p className="eyebrow" style={{ marginBottom: "22px" }}>
              Reckon brief
            </p>
            <h1 className="hero">{heroLine}</h1>
            <p className="hero-sub">{heroSub}</p>
          </div>
          <div className="brief-head-aside">
            <p className="band-label">You last checked</p>
            <p className="last-checked">
              {brief.last_open_at ? visitStamp(brief.last_open_at) : "First visit"}
            </p>
            <p className="band-note">{sessionsClosed(brief.sessions_since_last_open)}</p>
          </div>
        </header>

        <Accounting counts={counts} />

        <StartHere
          start={brief.start_here}
          busy={busy}
          onOpen={onOpen}
          onAcknowledge={onAcknowledge}
        />

        {needsYou.length > 0 ? (
          <section style={{ marginTop: "76px" }}>
            <SectionHead
              title="Needs you"
              count={counts.needs_you}
              gloss="Events and unexplained moves"
            />
            <div className="rows">
              {needsYou.map((card) => (
                <VerdictRow key={card.symbol} card={card} onOpen={onOpen} />
              ))}
            </div>
          </section>
        ) : (
          <section className="settled">
            <p className="settled-line">{brief.accounting_line}</p>
            <p className="checked-note" style={{ marginTop: "18px", fontSize: "15px" }}>
              Everything on your watchlist was checked. Nothing was left out, and
              nothing is outside what Reckon can account for.
            </p>
          </section>
        )}

        {groups.length > 0 && (
          <section style={{ marginTop: "76px" }}>
            <SectionHead
              title="Explained"
              count={brief.explained.count}
              gloss="One line per explanation"
            />
            <div className="rows">
              {groups.map((group) => (
                <ExplainedGroup
                  key={group.sector_index || group.kind}
                  group={group}
                  onOpen={onOpen}
                />
              ))}
            </div>
          </section>
        )}

        {brief.quiet.count > 0 && (
          <section style={{ marginTop: "72px" }}>
            <SectionHead title="Quiet" count={brief.quiet.count} tone="muted" />
            <div className="quiet-body">
              <p className="quiet-line">
                Nothing outside their normal range, and close to the market.
              </p>
              <div className="chips">
                {brief.quiet.symbols.map((card) => (
                  <button
                    className="chip"
                    key={card.symbol}
                    onClick={() => onOpen(card.symbol)}
                  >
                    {card.symbol}
                  </button>
                ))}
              </div>
            </div>
          </section>
        )}

        {brief.cant_say.count > 0 && (
          <section style={{ marginTop: "72px" }}>
            <SectionHead
              title="Couldn’t confidently evaluate"
              count={brief.cant_say.count}
              gloss="No claim is made here"
              tone="grey"
            />
            <div className="rows">
              {brief.cant_say.reasons.map((reason) => (
                <div className="uncertain" key={reason.reason}>
                  <div className="row-gutter">
                    <div className="uncertain-dash">&mdash;</div>
                    <div className="uncertain-tag">Can&rsquo;t say</div>
                  </div>
                  <div className="row-body">
                    <p className="uncertain-label">{reason.label}</p>
                    <p className="uncertain-detail">{reason.detail}</p>
                    <div className="chips">
                      {reason.symbols.map((code) => (
                        <button
                          className="chip grey"
                          key={code}
                          onClick={() => onOpen(code)}
                        >
                          {code}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="checked">
          <div className="checked-main">
            <p className="sect-title" style={{ marginBottom: "20px" }}>
              What Reckon checked
            </p>
            <p className="checked-title">Everything on your watchlist was checked.</p>
            <p className="checked-report">{brief.silence_report}</p>
            <p className="checked-note">
              Nothing was filtered out. Silence is a result, not a failure.
            </p>
          </div>
          <div className="checked-aside">
            {countCells(counts).map((cell) => (
              <div className="ledger-row" key={cell.key}>
                <span className="ledger-label">{cell.label}</span>
                <span className="ledger-value" style={{ color: cell.color }}>
                  {cell.n}
                </span>
              </div>
            ))}
            <button className="btn btn-ghost" onClick={onViewEverything}>
              View everything &rarr;
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
