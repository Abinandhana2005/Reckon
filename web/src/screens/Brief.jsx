import React, { useState } from "react";
import { EpistemicTag, Section, StockRow, toneOf } from "../components/ui.jsx";
import { lastLookedSentence, movement, sessionsSentence, signedPercent } from "../format.js";

/**
 * The brief.
 *
 * The order is the product's argument: what you missed, what needs you, what
 * was explained, what was quiet, what could not be checked. Explained comes as
 * one line per explanation rather than one card per stock, because the unit
 * that deserves the reader's attention is the reason, not the ticker.
 */

function anySessionsAway(brief) {
  const everyCard = [
    ...brief.needs_you,
    ...brief.quiet.symbols,
    ...brief.cant_say.symbols,
    ...brief.explained.groups.flatMap((group) => group.symbols),
  ];
  return everyCard.length ? Math.max(...everyCard.map((card) => card.sessions_away)) : 0;
}

function BriefHead({ brief }) {
  // Two spans exist and they are not always the same. `sessions_since_last_open`
  // is how long since you opened the app; the window on the cards is how far
  // back your reference point sits. The header describes whichever one the
  // verdicts were actually measured over, so the numbers below it line up.
  const sinceOpen = brief.sessions_since_last_open;
  const window = anySessionsAway(brief);
  const marketReturn =
    brief.market?.return_since_last_open ?? brief.market?.return_over_window;

  return (
    <header className="brief-head">
      <p className="lead">{lastLookedSentence(brief.last_open_at)}</p>
      <p>
        {sinceOpen > 0
          ? sessionsSentence(sinceOpen)
          : window > 0
            ? `Measured against ${window === 1 ? "the last session" : `${window} sessions ago`}.`
            : sessionsSentence(sinceOpen)}
      </p>
      {marketReturn !== null && marketReturn !== undefined && (
        <p>The market {movement(marketReturn)}.</p>
      )}
      {/* When nothing needs the reader, the settled block below carries this
          line at full size. Printing it twice weakens it. */}
      {brief.counts.needs_you > 0 && (
        <p className="accounting">{brief.accounting_line}</p>
      )}
    </header>
  );
}

function ExplainedGroup({ group, onOpen }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="group">
      <button className="group-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`chev ${open ? "open" : ""}`} aria-hidden="true">
          ▶
        </span>
        <span className="group-line">{group.line}</span>
        <span className="section-count">{group.size}</span>
      </button>
      {open && (
        <div className="group-body">
          <p className="group-hint">
            Open any of these to see why it wasn’t flagged.
          </p>
          <div className="rows">
            {group.symbols.map((card) => (
              <StockRow key={card.symbol} card={card} onOpen={onOpen} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CompactGroup({ label, line, symbols, onOpen, tone }) {
  const [open, setOpen] = useState(false);
  if (!symbols.length) return null;
  return (
    <div className="group">
      <button className="group-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`chev ${open ? "open" : ""}`} aria-hidden="true">
          ▶
        </span>
        <span className="group-line">{line}</span>
      </button>
      {open && (
        <div className="compact">
          {symbols.map((card) => (
            <button
              key={card.symbol}
              className={`chip ${tone}`}
              onClick={() => onOpen(card.symbol)}
            >
              {card.symbol}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Brief({ brief, onOpen }) {
  const needsYou = brief.needs_you;
  const groups = brief.explained.groups;

  return (
    <div>
      <BriefHead brief={brief} />

      {needsYou.length > 0 ? (
        <Section label="Needs you" count={needsYou.length}>
          <div className="rows">
            {needsYou.map((card) => (
              <StockRow key={card.symbol} card={card} onOpen={onOpen} />
            ))}
          </div>
        </Section>
      ) : (
        <div className="settled">
          <p className="display">{brief.accounting_line}</p>
          <p className="muted">
            Everything on your watchlist was checked. Nothing was left out.
          </p>
        </div>
      )}

      {groups.length > 0 && (
        <Section label="Explained" count={brief.explained.count}>
          {groups.map((group) => (
            <ExplainedGroup
              key={group.sector_index || group.kind}
              group={group}
              onOpen={onOpen}
            />
          ))}
        </Section>
      )}

      {brief.quiet.count > 0 && (
        <Section label="Quiet" count={brief.quiet.count}>
          <CompactGroup
            line="Nothing outside its normal range."
            symbols={brief.quiet.symbols}
            onOpen={onOpen}
            tone=""
          />
        </Section>
      )}

      {brief.cant_say.count > 0 && (
        <Section label="Couldn’t confidently evaluate" count={brief.cant_say.count}>
          <CompactGroup
            line={brief.cant_say.line}
            symbols={brief.cant_say.symbols}
            onOpen={onOpen}
            tone="grey"
          />
        </Section>
      )}
    </div>
  );
}
