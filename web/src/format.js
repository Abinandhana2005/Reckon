/**
 * Turning API numbers into sentences a person can read.
 *
 * The rule the backend holds itself to applies here too: describe, never
 * explain. "Larger than 94% of comparable moves" is a statement about a
 * distribution. "Because the market fell" would be a claim about cause, and
 * nothing in the evidence supports one.
 */

export function percent(value, places = 1) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(places)}%`;
}

/** Signed, for a change the reader is meant to compare against zero. */
export function signedPercent(value, places = 1) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${(Math.abs(value) * 100).toFixed(places)}%`;
}

export function movement(value) {
  if (value === null || value === undefined) return "moved by an unknown amount";
  if (value === 0) return "was flat";
  return `${value > 0 ? "rose" : "fell"} ${percent(Math.abs(value))}`;
}

/** A percentile, said out loud. */
export function percentileSentence(comparison, subject) {
  if (!comparison) return null;
  const { percentile, sample_size: samples } = comparison;
  if (percentile >= 100) {
    return `${subject} was larger than every one of the ${samples} comparable moves on record.`;
  }
  if (percentile < 1) {
    return `${subject} was smaller than almost every comparable move on record.`;
  }
  return `${subject} was larger than ${Math.round(percentile)}% of ${samples} comparable moves.`;
}

export function isUnusual(comparison) {
  return Boolean(comparison && comparison.percentile >= 90);
}

const DAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parse(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function dayAndDate(iso) {
  const date = parse(iso);
  if (!date) return null;
  return `${DAY[date.getDay()]} ${date.getDate()} ${MONTH[date.getMonth()]}`;
}

export function shortDate(iso) {
  const date = parse(iso);
  if (!date) return "—";
  return `${date.getDate()} ${MONTH[date.getMonth()]} ${date.getFullYear()}`;
}

/** "You last looked Tuesday evening." — the narrative clock, not the anchor. */
export function lastLookedSentence(iso) {
  const date = parse(iso);
  if (!date) return "This is your first look.";
  const hour = date.getHours();
  const part = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening";
  return `You last looked ${DAY[date.getDay()]} ${part}.`;
}

export function sessionsSentence(count) {
  if (!count) return "No trading sessions have closed since.";
  return count === 1 ? "1 trading session." : `${count} trading sessions.`;
}

export function plural(count, one, many) {
  return count === 1 ? one : many;
}
