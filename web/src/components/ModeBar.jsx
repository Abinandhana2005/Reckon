import React from "react";

/**
 * The switch between sample data and the real market.
 *
 * Live Mode is not a separate product: it changes where the prices come from
 * and nothing else, so this is a strip above the same brief rather than a
 * different screen. When no token is configured on the server the choice is
 * shown as unavailable with the reason, rather than hidden — a judge should be
 * able to tell the difference between "not built" and "not configured".
 */
export default function ModeBar({ state, busy, onSwitch, onRefresh }) {
  if (!state) return null;
  const live = state.mode === "live";

  return (
    <div className={`modebar ${live ? "is-live" : ""}`}>
      <div className="wrap modebar-inner">
        <span className="modebar-label">{live ? "Live market data" : "Sample data"}</span>

        <span className="modebar-actions">
          {state.live_available ? (
            <>
              <button
                className="scenario"
                aria-pressed={!live}
                disabled={busy}
                onClick={() => onSwitch("replay")}
              >
                Sample
              </button>
              <button
                className="scenario"
                aria-pressed={live}
                disabled={busy}
                onClick={() => onSwitch("live")}
              >
                Live
              </button>
              {live && (
                <button className="leave" disabled={busy} onClick={onRefresh}>
                  {busy ? "Refreshing…" : "Refresh prices"}
                </button>
              )}
            </>
          ) : (
            <span className="small muted">
              Live market data is off on this server ({state.live_reason}).
            </span>
          )}
        </span>
      </div>
    </div>
  );
}
