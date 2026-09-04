import React from "react";
import { signedPercent } from "../format.js";

/**
 * The epistemic tag, and the colour rule that goes with it.
 *
 * Unknown means two different things and they must not look alike: a move
 * nothing accounts for is amber and asks for attention, while data that cannot
 * be trusted is grey and asks for none. Collapsing them into one colour would
 * dress up an absence of evidence as a finding.
 */
export function toneOf(card) {
  if (card.verdict === "UNEXPLAINED") return "amber";
  if (card.verdict === "CANT_SAY") return "grey";
  return "";
}

function tagClass(card) {
  if (card.verdict === "UNEXPLAINED") return "unknown-amber";
  if (card.verdict === "CANT_SAY") return "unknown-grey";
  if (card.epistemic === "KNOWN") return "known";
  return "inferred";
}

export function EpistemicTag({ card }) {
  return (
    <span className={`tag ${tagClass(card)}`}>
      <span className="dot" aria-hidden="true" />
      {card.epistemic_label}
    </span>
  );
}

export function StockRow({ card, onOpen }) {
  const tone = toneOf(card);
  return (
    <button className={`row ${tone}`} onClick={() => onOpen(card.symbol)}>
      <span className="row-top">
        <span className="ticker">{card.symbol}</span>
        <span className="company">{card.name}</span>
        {card.change !== null && card.change !== undefined && (
          <span className="change tabular">{signedPercent(card.change)}</span>
        )}
        <EpistemicTag card={card} />
      </span>
      <p className="row-headline">{card.headline}</p>
    </button>
  );
}

export function Section({ label, count, children }) {
  return (
    <section className="section">
      <header className="section-head">
        <h2 className="section-label">{label}</h2>
        {count !== undefined && <span className="section-count">{count}</span>}
      </header>
      {children}
    </section>
  );
}

export function Loading({ lines = 3 }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }).map((_, index) => (
        <div
          className="skeleton"
          key={index}
          style={{ width: `${88 - index * 16}%`, height: index === 0 ? "2rem" : undefined }}
        />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="state">
      <p className="display">Something went wrong.</p>
      <p>{error?.message || "Unknown error."}</p>
      {onRetry && (
        <p style={{ marginTop: "1.5rem" }}>
          <button className="action" onClick={onRetry}>
            Try again
          </button>
        </p>
      )}
    </div>
  );
}
