import React from "react";

/**
 * The first screen, and the only place the demo and the real watchlist are
 * offered as a choice.
 *
 * The demo is not a tour or a slideshow. It opens the same brief, built by the
 * same classifier, over a preloaded watchlist belonging to a separate session.
 */
export default function Welcome({ onDemo, onBuild, busy }) {
  return (
    <div className="welcome">
      <h1 className="display">Reckon</h1>
      <p className="lede">What happened while you were away?</p>

      <p className="muted" style={{ maxWidth: "30rem", marginTop: "1.5rem" }}>
        Reckon checks every stock you follow and tells you which ones deserve your
        attention — and why the rest didn’t.
      </p>

      <div className="choices">
        <button
          className="choice choice-primary"
          onClick={onDemo}
          disabled={busy === "demo"}
        >
          <h2>{busy === "demo" ? "Preparing the demo…" : "Try the demo"}</h2>
          <p>
            Sixteen stocks on a day the market fell. See the real brief, not a
            walkthrough. Your own watchlist stays untouched.
          </p>
        </button>

        <button className="choice" onClick={onBuild} disabled={busy === "build"}>
          <h2>Build my watchlist</h2>
          <p>
            Add the stocks you follow. Reckon will tell you what changed the next
            time you look.
          </p>
        </button>
      </div>
    </div>
  );
}
