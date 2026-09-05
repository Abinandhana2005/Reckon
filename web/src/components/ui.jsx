import React from "react";
import { signedPercent } from "../format.js";

/**
 * The vocabulary every screen shares.
 *
 * Colour encodes certainty, not direction. Amber is reserved for the one
 * verdict Reckon cannot account for; grey for data it does not trust; the
 * blue-slate `--known` for a confirmed fact. Everything it can explain stays in
 * ink, including a stock down four percent, because an explained fall is not an
 * alarm.
 */

export const VERDICT_LABEL = {
  UNEXPLAINED: "Unexplained",
  EVENT: "Event",
  WITH_MARKET: "With market",
  WITH_SECTOR: "With sector",
  QUIET: "Quiet",
  CANT_SAY: "Can’t say",
};

/** The CSS variable a verdict is drawn in. */
export function toneVar(verdict) {
  if (verdict === "UNEXPLAINED") return "var(--amber)";
  if (verdict === "CANT_SAY") return "var(--grey)";
  if (verdict === "EVENT") return "var(--known)";
  return "var(--ink-muted)";
}

/** The wash class for a verdict's filled surfaces. */
export function toneClass(verdict) {
  if (verdict === "UNEXPLAINED") return "amber";
  if (verdict === "CANT_SAY") return "grey";
  return "known";
}

export function verdictLabel(card) {
  return VERDICT_LABEL[card.verdict] || card.verdict;
}

/**
 * A section header: title, count, a hairline that runs to the edge, and an
 * optional gloss explaining what the section is for.
 */
export function SectionHead({ title, count, gloss, tone }) {
  return (
    <div className="sect">
      <h2 className={`sect-title ${tone || ""}`}>{title}</h2>
      {count !== undefined && count !== null && (
        <span className="sect-count">{count}</span>
      )}
      <div className="sect-fill" />
      {gloss && <span className="sect-gloss">{gloss}</span>}
    </div>
  );
}

/**
 * One verdict, as a row.
 *
 * The gutter carries the number and the verdict label; the body carries the
 * ticker, the sentence Reckon asserts, and how it knows. Reading down the
 * gutter alone tells you what happened to each stock.
 */
export function VerdictRow({ card, onOpen }) {
  return (
    <button className="row" onClick={() => onOpen(card.symbol)}>
      <span className="row-gutter">
        <span className="row-change tabular">
          {card.change === null || card.change === undefined
            ? "—"
            : signedPercent(card.change)}
        </span>
        <span className="row-verdict" style={{ color: toneVar(card.verdict) }}>
          {verdictLabel(card)}
        </span>
      </span>
      <span className="row-body">
        <span className="row-id">
          <span className="row-ticker">{card.symbol}</span>
          <span className="row-name">{card.name}</span>
        </span>
        <span className="row-headline">{card.headline}</span>
        <span className="row-epistemic">{card.epistemic_label}</span>
      </span>
      <span className="row-arrow" aria-hidden="true">
        →
      </span>
    </button>
  );
}

/** The loading state: the shape of a brief, before the brief. */
export function Loading({ note }) {
  const bars = [
    { height: 44, width: "62%", top: 0 },
    { height: 16, width: "38%", top: 20 },
    { height: 150, width: "100%", top: 48 },
    { height: 16, width: "70%", top: 40 },
    { height: 16, width: "52%", top: 16 },
  ];
  return (
    <div className="page" aria-busy="true" aria-label="Loading">
      {bars.map((bar, index) => (
        <div
          className="shimmer"
          key={index}
          style={{ height: bar.height, width: bar.width, marginTop: bar.top }}
        />
      ))}
      {note && <p className="loading-note">{note}</p>}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="state">
      <p className="eyebrow">Something went wrong</p>
      <h1 className="state-title" style={{ marginTop: "20px" }}>
        Reckon couldn’t finish.
      </h1>
      <p className="state-body">{error?.message || "Unknown error."}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
