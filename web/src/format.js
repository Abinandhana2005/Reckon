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

/** A percentile as English reads it: 1st, 22nd, 53rd, 11th. */
export function ordinal(value) {
  const number = Math.round(value);
  if (number % 100 >= 11 && number % 100 <= 13) return `${number}th`;
  return `${number}${ { 1: "st", 2: "nd", 3: "rd" }[number % 10] || "th" }`;
}

export function isUnusual(comparison) {
  return Boolean(comparison && comparison.percentile >= 90);
}

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function parse(iso) {
  if (!iso) return null;
  // Live timestamps are naive UTC instants. Appending Z keeps their provenance
  // truthful when the browser is running outside the exchange timezone.
  //
  // Sample timestamps are not instants at all: the fixture's calendar closes at
  // a synthetic 15:30 that means nothing in any timezone, so those are rendered
  // with `shortDate` and never with a time. Reading one as UTC and converting
  // it announced that the sample market closes at 9 PM.
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "31 Jul, 16:04" — the band and rails, where the label supplies the context. */
export function stamp(iso) {
  const date = parse(iso);
  if (!date) return "—";
  return `${date.getDate()} ${MONTH[date.getMonth()]}, ${String(date.getHours()).padStart(2, "0")}:${String(
    date.getMinutes(),
  ).padStart(2, "0")}`;
}

/** "Tue 28 Jul, 09:12" — a visit, where the weekday is the useful part. */
export function visitStamp(iso) {
  const date = parse(iso);
  if (!date) return "Never";
  return `${DAY[date.getDay()]} ${stamp(iso)}`;
}

export function dateTime(iso) {
  const date = parse(iso);
  if (!date) return "—";
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
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
  return `You last looked ${dateTime(iso)}.`;
}

export function sessionsSentence(count) {
  if (!count) return "No trading sessions have closed since.";
  return count === 1 ? "1 trading session." : `${count} trading sessions.`;
}

/** "3 trading sessions have closed since." */
export function sessionsClosed(count) {
  if (!count) return "No trading sessions have closed since.";
  return count === 1
    ? "1 trading session has closed since."
    : `${count} trading sessions have closed since.`;
}
