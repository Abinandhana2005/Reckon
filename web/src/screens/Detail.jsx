import React from "react";
import { Sparkline } from "../components/Sparkline.jsx";
import { SectionHead, toneClass, toneVar, verdictLabel } from "../components/ui.jsx";
import {
  ordinal,
  percent,
  percentileSentence,
  shortDate,
  signedPercent,
  stamp,
} from "../format.js";

/**
 * Why Reckon reached this conclusion.
 *
 * Every sentence here is either copied from the API or derived from a number
 * the API returned. Nothing on this screen reasons about cause: the closest it
 * comes is "consistent with", which is a statement about two numbers moving
 * together and is the strongest claim the evidence supports.
 */

const SURFACED = new Set(["EVENT", "UNEXPLAINED"]);

const STATUS_MARK = { PASSED: "✓", DECIDED: "●", SKIPPED: "–", UNAVAILABLE: "?" };

const STATUS_WORD = {
  PASSED: "checked · did not decide",
  DECIDED: "decided the verdict",
  SKIPPED: "not reached",
  UNAVAILABLE: "not available",
};

/**
 * Why a surfaced card was surfaced, stated as what the classifier actually did.
 *
 * The API answers "why not?" for everything it explained away; for the two
 * verdicts it does surface it simply says so. Restating the card's own summary
 * would be repetition, so this describes the order of checks that ended in this
 * verdict -- a fact about the classifier, not a claim about cause.
 */
function whySurfaced(card) {
  if (card.verdict === "EVENT") {
    return "A confirmed event is on record for this period. Events are facts, so they outrank every comparison and are always surfaced.";
  }
  return "Reckon compared this move against the stock's own range, against the market, and against its sector. None of them account for it, so it was surfaced rather than explained away.";
}

/**
 * The route the classifier took, in the order it took it.
 *
 * Six checks, always all six, including the ones that never ran. A trace that
 * hid its skipped steps would read as though every verdict were reached the
 * same way, when the whole point of the precedence is that it stops early.
 */
function DecisionTrace({ trace, verdict }) {
  if (!trace || !trace.length) return null;
  return (
    <section style={{ marginTop: "56px" }}>
      <SectionHead
        title="Why Reckon reacted"
        gloss="The checks, in the order they ran"
      />
      <ol className="trace">
        {trace.map((step, index) => {
          const dim = step.status === "SKIPPED" || step.status === "UNAVAILABLE";
          const decided = step.status === "DECIDED";
          return (
            <li
              className={
                "trace-step" +
                (decided ? " decided " + toneClass(verdict) : "") +
                (dim ? " dim" : "")
              }
              key={step.key}
            >
              <div className="trace-mark">
                <div className="trace-n">
                  {String(index + 1).padStart(2, "0")}
                </div>
                <div className="trace-glyph" aria-hidden="true">
                  {STATUS_MARK[step.status] || "•"}
                </div>
              </div>
              <div className="trace-body">
                <p className="trace-label">{step.label}</p>
                <p className="trace-status">{STATUS_WORD[step.status] || ""}</p>
                <p className="trace-note">{step.note}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** The evidence behind the verdict, as label/value hairlines. */
function supportingNumbers(evidence) {
  const rows = [];
  const has = (value) => value !== null && value !== undefined;

  if (has(evidence.adjusted_return)) {
    rows.push({
      label: "This stock",
      value: signedPercent(evidence.adjusted_return),
    });
  }
  if (has(evidence.market_return)) {
    rows.push({ label: "The market", value: signedPercent(evidence.market_return) });
  }
  if (evidence.sector_available && has(evidence.sector_return)) {
    rows.push({ label: "Its sector", value: signedPercent(evidence.sector_return) });
  }
  if (evidence.own) {
    rows.push({
      label: "Its usual move over this span",
      value: percent(evidence.own.typical_abs),
    });
    rows.push({
      label: "Where this move ranks",
      value: ordinal(evidence.own.percentile) + " pctl",
      color: evidence.own.percentile >= 90 ? "var(--amber)" : undefined,
    });
  }
  if (evidence.vs_market) {
    rows.push({
      label: "Gap to the market ranks",
      value: ordinal(evidence.vs_market.percentile) + " pctl",
      color: evidence.vs_market.percentile >= 90 ? "var(--amber)" : undefined,
    });
  }
  if (evidence.adjustment_factor !== 1) {
    rows.push({
      label: "Adjusted for a corporate action",
      value: "×" + evidence.adjustment_factor.toFixed(2),
    });
  }
  rows.push({ label: "Sessions of history", value: String(evidence.history_bars) });
  return rows;
}

/**
 * Where the numbers came from, and how old they are.
 *
 * The moments stay apart for the same reason they do on the brief: they answer
 * different questions, and merging them would let a fetch a minute ago stand in
 * for a market observation from Friday.
 */
function freshnessRows(detail) {
  const live = detail.data_source === "live";
  const provenance = detail.provenance || {};
  if (!live) {
    return [
      {
        label: "Data source",
        value:
          detail.simulation && detail.simulation.active
            ? "Demo · sample fixture"
            : "Sample fixture",
      },
      { label: "Analysis through", value: shortDate(detail.as_of) },
    ];
  }
  return [
    { label: "Data source", value: provenance.provider || "Yahoo Finance" },
    { label: "Data fetched", value: stamp(provenance.data_fetched_at) },
    {
      label: "Latest available quote",
      value: provenance.latest_available_quote_at
        ? stamp(provenance.latest_available_quote_at)
        : "Unavailable",
    },
    {
      label: "Analysis through",
      value: stamp(provenance.latest_completed_session_at),
    },
  ];
}

export default function Detail({ detail, busy, onBack, onAcknowledge, onRefresh }) {
  const card = detail.card;
  const evidence = detail.evidence;
  const sectorName = detail.sector_name;
  const surfaced = SURFACED.has(card.verdict);
  const tone = toneClass(card.verdict);
  const acking = busy === "ack:" + card.symbol;

  const whyTitle = surfaced
    ? "Why this was surfaced"
    : card.verdict === "CANT_SAY"
      ? "Why no claim was made"
      : "Why this wasn’t flagged";

  return (
    <article className="wrap detail">
      <button className="back" onClick={onBack}>
        &larr; Back to the brief
      </button>

      <header className="detail-head">
        <div className="detail-id">
          <div className="detail-ticker">{card.symbol}</div>
          <div className="detail-name">{card.name}</div>
        </div>
        <div className="detail-figure">
          <div className="detail-change tabular">
            {card.change === null || card.change === undefined
              ? "—"
              : signedPercent(card.change)}
          </div>
          <div className="detail-tags">
            <span className="detail-tag" style={{ color: toneVar(card.verdict) }}>
              {verdictLabel(card)}
            </span>
            <span className="detail-tag epistemic">{card.epistemic_label}</span>
          </div>
        </div>
      </header>

      <div className="detail-cols">
        <div className="detail-main">
          <h1 className="verdict-statement">{card.headline}</h1>
          <p className="detail-prose">{card.detail}</p>

          <div className={"why " + tone}>
            <p className="why-title">{whyTitle}</p>
            <p className="why-text">
              {surfaced ? whySurfaced(card) : detail.why_not_flagged}
            </p>
            {!surfaced && card.verdict !== "CANT_SAY" && (
              <p className="why-seen">
                Reckon saw this move. It was compared and accounted for, not
                overlooked.
              </p>
            )}
          </div>

          <DecisionTrace trace={detail.decision_trace} verdict={card.verdict} />

          {detail.sparkline && detail.sparkline.length > 1 && (
            <section style={{ marginTop: "56px" }}>
              <SectionHead
                title={"Last " + detail.sparkline.length + " sessions"}
                gloss="Your reference point is marked"
              />
              <div style={{ marginTop: "20px" }}>
                <Sparkline
                  points={detail.sparkline}
                  anchorDate={detail.anchor && detail.anchor.at}
                />
              </div>
            </section>
          )}
        </div>

        <aside className="rail">
          <div className="rail-block">
            <p className="rail-title">Supporting numbers</p>
            {supportingNumbers(evidence).map((row) => (
              <div className="rail-row" key={row.label}>
                <span className="rail-row-label">{row.label}</span>
                <span className="rail-row-value" style={{ color: row.color }}>
                  {row.value}
                </span>
              </div>
            ))}
            {evidence.own && (
              <p className="rail-note">
                {percentileSentence(evidence.own, "Its move")}{" "}
                {evidence.vs_market
                  ? percentileSentence(
                      evidence.vs_market,
                      "The gap between it and the market",
                    )
                  : ""}
              </p>
            )}
            {!evidence.sector_available && (
              <p className="rail-note">
                No sector index is held for this stock, so only the market
                comparison was available.
              </p>
            )}
            {evidence.events_in_window && evidence.events_in_window.length > 0 && (
              <p className="rail-note">
                {evidence.events_in_window
                  .map((event) => event.detail + " (" + shortDate(event.on_date) + ")")
                  .join(" · ")}
              </p>
            )}
          </div>

          <div className="rail-block">
            <p className="rail-title">Freshness</p>
            {freshnessRows(detail).map((row) => (
              <div className="rail-stack" key={row.label}>
                <div className="rail-stack-label">{row.label}</div>
                <div className="rail-stack-value">{row.value}</div>
              </div>
            ))}
            <p className="rail-note">
              {detail.data_source === "live"
                ? "Prices are fetched when you ask for them, not streamed. Verdicts are measured against the latest completed session."
                : "A fixed calendar, not the market. Verdicts are measured against the latest completed session."}
            </p>
            {detail.data_source === "live" && onRefresh && (
              <p className="rail-note">
                <button className="linkish" onClick={onRefresh} disabled={Boolean(busy)}>
                  {busy === "mode" ? "Refreshing…" : "Refresh from the provider"}
                </button>
              </p>
            )}
          </div>

          <div className="rail-actions">
            {onAcknowledge && (
              <button
                className="btn"
                disabled={acking}
                onClick={() => onAcknowledge(card.symbol, card.snapshot_id)}
              >
                {acking ? "Marking…" : "Mark as seen"}
              </button>
            )}
            <p>
              Compared against your reference point of{" "}
              {shortDate(detail.anchor && detail.anchor.at)} —{" "}
              {evidence.sessions_away}{" "}
              {evidence.sessions_away === 1 ? "session" : "sessions"} ago.
            </p>
          </div>
        </aside>
      </div>
    </article>
  );
}
