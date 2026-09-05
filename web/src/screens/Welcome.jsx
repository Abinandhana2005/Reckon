import React from "react";

/**
 * The way in.
 *
 * Two questions, asked one at a time. First: do you want to see what Reckon
 * does, or start using it? Only if the answer is "start using it" does the
 * second question appear -- real market data or sample data -- because a
 * first-time reader should not have to understand where prices come from before
 * they understand what the product is for.
 */

const POINTS = [
  ["Checks every watched stock.", "Sixteen watched, sixteen accounted for."],
  ["Explains what it can.", "One line per explanation, not one card per ticker."],
  ["Stays quiet otherwise.", "Silence is a result, not a failure."],
  ["Says when it can’t say.", "Doubt outranks confidence."],
];

export default function Welcome({
  step,
  busy,
  live,
  themeLabel,
  onToggleTheme,
  onDemo,
  onBuild,
  onBack,
  onChoose,
}) {
  return (
    <div className="welcome">
      <div className="welcome-top">
        <span className="wordmark">RECKON</span>
        <button className="theme-toggle" onClick={onToggleTheme}>
          {themeLabel}
        </button>
      </div>

      <div className="welcome-body">
        {step === "source" ? (
          <SourceChoice busy={busy} live={live} onBack={onBack} onChoose={onChoose} />
        ) : (
          <Intro busy={busy} onDemo={onDemo} onBuild={onBuild} />
        )}
      </div>
    </div>
  );
}

function Intro({ busy, onDemo, onBuild }) {
  return (
    <div className="welcome-inner">
      <div className="welcome-main">
        <p className="eyebrow" style={{ marginBottom: "28px" }}>
          A market briefing, not a dashboard
        </p>
        <h1 className="welcome-hero">
          What did you
          <br />
          miss while you
          <br />
          were away?
        </h1>
        <p className="welcome-lede">
          Reckon checks every stock you follow, tells you which ones deserve your
          attention — and, just as importantly, why the rest didn’t.
        </p>

        <div className="welcome-actions">
          <button className="btn" onClick={onDemo} disabled={busy === "demo"}>
            {busy === "demo" ? "Preparing…" : "Try the demo"}
          </button>
          <button
            className="btn btn-ghost"
            onClick={onBuild}
            disabled={Boolean(busy)}
          >
            Build my watchlist
          </button>
        </div>
        <p className="welcome-note">
          The demo is a guided showcase: sixteen prepared issuers on a day the
          market fell, run through the real brief. Nothing in it touches your own
          watchlist.
        </p>
      </div>

      <div className="welcome-aside">
        <div className="welcome-aside-inner">
          <p className="eyebrow" style={{ marginBottom: "22px" }}>
            What it does
          </p>
          {POINTS.map(([title, note]) => (
            <div className="welcome-point" key={title}>
              <p className="welcome-point-title">{title}</p>
              <p className="welcome-point-note">{note}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SourceChoice({ busy, live, onBack, onChoose }) {
  const available = live && live.live_available;
  return (
    <div className="welcome-inner">
      <div className="welcome-main">
        <button className="back" onClick={onBack}>
          &larr; Back
        </button>
        <h1 className="welcome-hero" style={{ marginTop: "28px" }}>
          Where should
          <br />
          the prices
          <br />
          come from?
        </h1>
        <p className="welcome-lede">
          This is the data your watchlist is built from. You can change it later,
          and each source keeps its own watchlist.
        </p>
      </div>

      <div className="welcome-aside">
        <div className="welcome-aside-inner">
          <p className="eyebrow" style={{ marginBottom: "22px" }}>
            Data source
          </p>
          <button
            className="source"
            style={{ width: "100%", marginBottom: "12px" }}
            disabled={!available || busy === "mode"}
            onClick={() => onChoose("live")}
          >
            <span className="source-top">
              <span className="source-dot" />
              <span className="source-title">Live data</span>
              <span className="source-state">
                {available ? "Recommended" : "Unavailable"}
              </span>
            </span>
            <span className="source-blurb">
              Real NSE market data via Yahoo Finance. Analysis runs on the latest
              completed trading session.
            </span>
            {!available && live && live.live_reason && (
              <span className="source-blurb" style={{ color: "var(--amber)" }}>
                {live.live_reason}
              </span>
            )}
          </button>
          <button
            className="source"
            style={{ width: "100%" }}
            disabled={busy === "mode"}
            onClick={() => onChoose("replay")}
          >
            <span className="source-top">
              <span className="source-dot" />
              <span className="source-title">Sample data</span>
              <span className="source-state">No network needed</span>
            </span>
            <span className="source-blurb">
              Deterministic sample market data. The same classifier and the same
              brief, with results that never change between runs.
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
