import React from "react";
import { Sparkline } from "../components/Sparkline.jsx";
import { EpistemicTag, toneOf } from "../components/ui.jsx";
import {
  isUnusual,
  percent,
  percentileSentence,
  shortDate,
  signedPercent,
} from "../format.js";

/**
 * Why Reckon decided what it decided.
 *
 * Every sentence here is either copied from the API or derived from a number
 * the API returned. Nothing on this screen reasons about cause: the closest it
 * comes is "consistent with", which is a statement about two numbers moving
 * together and is the strongest claim the evidence supports.
 */

const SURFACED = new Set(["EVENT", "UNEXPLAINED"]);

/**
 * Why a surfaced card was surfaced, stated as what the classifier actually did.
 *
 * The API answers "why not?" for everything it explained away, but for the two
 * verdicts it does surface it simply says so. Restating the card's own summary
 * here would be repetition, so this describes the order of checks that ended in
 * this verdict -- which is a fact about the classifier, not a claim about cause.
 */
function whySurfaced(card) {
  if (card.verdict === "EVENT") {
    return "A confirmed event is on record for this period. Events are facts, so they outrank every comparison and are always surfaced.";
  }
  return "Reckon compared this move against the stock's own range, against the market, and against its sector. None of them account for it, so it was surfaced rather than explained away.";
}

function Evidence({ evidence, sectorName }) {
  const hasComparisons = evidence.own || evidence.vs_market;
  if (!hasComparisons) return null;

  return (
    <div className="panel">
      <h3>The numbers</h3>
      <dl className="ledger">
        <dt>This stock</dt>
        <dd>{signedPercent(evidence.adjusted_return)}</dd>

        <dt>The market</dt>
        <dd>{signedPercent(evidence.market_return)}</dd>

        {evidence.sector_available && evidence.sector_return !== null && (
          <>
            <dt>{sectorName || "Its sector"}</dt>
            <dd>{signedPercent(evidence.sector_return)}</dd>
          </>
        )}

        {evidence.own && (
          <>
            <dt>Its usual move over this span</dt>
            <dd>{percent(evidence.own.typical_abs)}</dd>
          </>
        )}

        {evidence.own && (
          <>
            <dt>Where this move ranks</dt>
            <dd className={isUnusual(evidence.own) ? "flag" : ""}>
              {Math.round(evidence.own.percentile)}th percentile
            </dd>
          </>
        )}

        {evidence.adjustment_factor !== 1 && (
          <>
            <dt>Adjusted for a corporate action</dt>
            <dd>×{evidence.adjustment_factor.toFixed(2)}</dd>
          </>
        )}
      </dl>

      <div style={{ marginTop: "1.2rem" }}>
        {evidence.own && (
          <p>{percentileSentence(evidence.own, "Its move")}</p>
        )}
        {evidence.vs_market && (
          <p>
            {percentileSentence(evidence.vs_market, "The gap between it and the market")}
          </p>
        )}
        {evidence.vs_sector && (
          <p>
            {percentileSentence(
              evidence.vs_sector,
              `The gap between it and ${sectorName || "its sector"}`,
            )}
          </p>
        )}
        {!evidence.sector_available && (
          <p className="muted">
            This stock has no sector index, so only the market comparison was available.
          </p>
        )}
      </div>
    </div>
  );
}

function Events({ evidence }) {
  const past = evidence.events_in_window || [];
  const upcoming = evidence.events_upcoming || [];
  if (!past.length && !upcoming.length) return null;

  return (
    <div className="panel">
      <h3>Confirmed events</h3>
      {past.map((event) => (
        <p key={`${event.kind}-${event.on_date}`}>
          <strong>{event.detail}</strong>
          <br />
          <span className="small muted">
            Recorded {shortDate(event.on_date)} · since you last looked
          </span>
        </p>
      ))}
      {upcoming.map((event) => (
        <p key={`up-${event.kind}-${event.on_date}`}>
          <strong>{event.detail}</strong>
          <br />
          <span className="small muted">Scheduled for {shortDate(event.on_date)}</span>
        </p>
      ))}
    </div>
  );
}

function DataQuality({ evidence, verdict }) {
  const stale = verdict === "CANT_SAY";
  if (!stale && evidence.history_bars >= 70) return null;

  return (
    <div className="panel grey">
      <h3>Data</h3>
      {evidence.history_bars < 70 && (
        <p>
          Only {evidence.history_bars} sessions of history are on record. Reckon needs
          more than that before it will compare a move against a normal range.
        </p>
      )}
      {evidence.quote_source && (
        <p className="small muted">
          Source: {evidence.quote_source}
          {evidence.volume_ratio
            ? ` · volume ${evidence.volume_ratio.toFixed(1)}× its recent median`
            : ""}
        </p>
      )}
    </div>
  );
}

export default function Detail({ detail, onBack }) {
  const { card, evidence, sector_name: sectorName } = detail;
  const surfaced = SURFACED.has(card.verdict);
  const tone = toneOf(card);

  return (
    <article>
      <a
        className="back"
        href="/"
        onClick={(event) => {
          event.preventDefault();
          onBack();
        }}
      >
        ← Back to the brief
      </a>

      <header className="detail-head">
        <div>
          <div className="ticker" style={{ fontSize: "1.05rem" }}>
            {card.symbol}
          </div>
          <div className="muted small">{card.name}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="detail-change">{signedPercent(card.change)}</div>
          <div style={{ marginTop: "0.35rem" }}>
            <EpistemicTag card={card} />
          </div>
        </div>
      </header>

      <h1 className="verdict-statement">{card.headline}</h1>
      <p className="detail-prose">{card.detail}</p>

      <div className={`panel ${tone}`}>
        <h3>{surfaced ? "Why this was surfaced" : "Why this wasn’t flagged"}</h3>
        <p>{surfaced ? whySurfaced(card) : detail.why_not_flagged}</p>
        {!surfaced && card.verdict !== "CANT_SAY" && (
          <p className="disclaimer">
            Reckon saw this move. It was compared and accounted for, not overlooked.
          </p>
        )}
      </div>

      <Evidence evidence={evidence} sectorName={sectorName} />
      <Events evidence={evidence} />
      <DataQuality evidence={evidence} verdict={card.verdict} />

      {detail.sparkline?.length > 1 && (
        <div className="panel">
          <h3>Last {detail.sparkline.length} sessions</h3>
          <Sparkline points={detail.sparkline} anchorDate={detail.anchor?.at} />
        </div>
      )}

      <p className="small muted" style={{ marginTop: "2rem" }}>
        Compared against your reference point of {shortDate(detail.anchor?.at)} —{" "}
        {evidence.sessions_away} {evidence.sessions_away === 1 ? "session" : "sessions"} ago.
      </p>
    </article>
  );
}
