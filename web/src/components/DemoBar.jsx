import React from "react";

/**
 * The demo's only control surface.
 *
 * Deliberately a thin strip rather than a panel: the demo exists to show the
 * product, so the product should stay the thing on screen. It appears only in
 * Demo Mode and never in a real session.
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
    <div className="demobar">
      <div className="wrap demobar-inner">
        <strong>Demo</strong>
        <div className="scenarios">
          {offered.map((scenario) => (
            <button
              key={scenario.key}
              className="scenario"
              aria-pressed={active.scenario === scenario.key && !active.stale}
              disabled={busy}
              onClick={() => onScenario(scenario.key)}
            >
              {scenario.title}
            </button>
          ))}
          <button
            className="scenario"
            aria-pressed={active.stale}
            disabled={busy}
            onClick={onStale}
            title="Show what happens when the data stops being trustworthy"
          >
            Stale data
          </button>
        </div>
        <button className="leave" onClick={onLeave}>
          Leave demo
        </button>
      </div>
    </div>
  );
}
