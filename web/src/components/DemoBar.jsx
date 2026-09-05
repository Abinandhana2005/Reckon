import React from "react";

/**
 * The demo's only control surface.
 *
 * Distinct by inversion rather than by tint: a full-bleed dark band directly
 * under the header, so a judge can never mistake a prepared scenario for the
 * reader's own watchlist. The brief below it renders exactly as it does in a
 * real session -- the point of the demo is to show the product, not a
 * substitute for it.
 *
 * The demo runs as a separate guest session on the server, so nothing done here
 * writes to a real watchlist or moves a real anchor.
 */

export const SCENARIOS = [
  { key: "market-wide", title: "Market fell" },
  { key: "banking sector", title: "Banking fell" },
  { key: "single-stock", title: "Unexplained move" },
  { key: "market rally", title: "Rally, one stock flat" },
];

export default function DemoBar({ available, active, busy, onScenario, onStale, onLeave }) {
  const offered = SCENARIOS.filter((scenario) =>
    available.some((label) => label.toLowerCase().includes(scenario.key)),
  );

  return (
    <div className="demoband">
      <div className="demoband-inner">
        <div className="demoband-top">
          <span className="demoband-chip">DEMO</span>
          <span className="demoband-note">
            This is a prepared scenario using deterministic sample data.
          </span>
          <button className="demoband-leave" onClick={onLeave}>
            Leave demo
          </button>
        </div>
        <div className="demoband-scenarios">
          <span className="demoband-label">Scenario</span>
          {offered.map((scenario) => (
            <button
              key={scenario.key}
              className="pill"
              aria-pressed={active.scenario === scenario.key && !active.stale}
              disabled={busy}
              onClick={() => onScenario(scenario.key)}
            >
              {scenario.title}
            </button>
          ))}
          <button
            className="pill"
            aria-pressed={active.stale}
            disabled={busy}
            onClick={onStale}
            title="Show what happens when the data stops being trustworthy"
          >
            Stale data
          </button>
        </div>
      </div>
    </div>
  );
}
