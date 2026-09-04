import React from "react";
import { shortDate } from "../format.js";

/**
 * Sixty sessions, with the moment the user last acknowledged marked.
 *
 * No axes, no grid, no candles. The line exists to place the move in the
 * stock's recent shape, and the dashed rule exists to say "you were here".
 */
export function Sparkline({ points, anchorDate }) {
  if (!points || points.length < 2) return null;

  const width = 600;
  const height = 80;
  const closes = points.map((p) => p.close);
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  const span = high - low || 1;

  const x = (index) => (index / (points.length - 1)) * width;
  const y = (close) => height - ((close - low) / span) * (height - 8) - 4;

  const path = points.map((p, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(p.close).toFixed(1)}`).join(" ");

  const anchorIndex = anchorDate
    ? points.findIndex((p) => p.date >= anchorDate.slice(0, 10))
    : -1;

  return (
    <>
      <svg
        className="spark"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Closing prices for the last ${points.length} sessions`}
      >
        {anchorIndex > 0 && (
          <line
            className="spark-anchor"
            x1={x(anchorIndex)}
            y1="0"
            x2={x(anchorIndex)}
            y2={height}
          />
        )}
        <path className="spark-line" d={path} />
      </svg>
      <div className="spark-caption">
        <span>{shortDate(points[0].date)}</span>
        {anchorIndex > 0 && <span>you last looked</span>}
        <span>{shortDate(points[points.length - 1].date)}</span>
      </div>
    </>
  );
}
