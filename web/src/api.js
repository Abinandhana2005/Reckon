/**
 * The API client, and the two identities the app can act as.
 *
 * Demo Mode is a separate guest session rather than a flag on the real one.
 * That is what makes "the demo cannot touch your watchlist" a fact about the
 * data rather than a promise about the UI: the demo's symbols, anchors and
 * simulation belong to a different user id on the server.
 *
 * localStorage holds session tokens and nothing else. Every piece of product
 * state -- watchlist, anchors, simulation -- stays server-side.
 */

const REAL_KEY = "reckon.session";
const DEMO_KEY = "reckon.demo.session";
const MODE_KEY = "reckon.mode";

export const DEMO_SYMBOLS = [
  "MRDB", "KVRB", "SGMF", "NWTC", "ORBS", "PRXD", "VNTP", "SETL",
  "ARDB", "DCNP", "KLSE", "TRFL", "MRGF", "SHDC", "BLWG", "ZNTH",
];

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function read(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* Private browsing. The session still works for this page load. */
  }
}

async function request(path, { token, method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { "X-Session-Token": token } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach Reckon. Check that the server is running.", 0);
  }

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(detail?.detail || `Request failed (${response.status})`, response.status);
  }
  return response.status === 204 ? null : response.json();
}

async function newSession() {
  const { session_token } = await request("/api/session", { method: "POST" });
  return session_token;
}

/** A stored token, verified still valid, or a fresh one. */
async function sessionFor(key) {
  const existing = read(key);
  if (existing) {
    try {
      await request("/api/me", { token: existing });
      return existing;
    } catch (error) {
      if (error.status !== 401) throw error;
    }
  }
  const token = await newSession();
  write(key, token);
  return token;
}

export const realSession = () => sessionFor(REAL_KEY);

/** Which identity the last page load was using, so a refresh stays put. */
export const storedMode = () => read(MODE_KEY);
export const rememberMode = (mode) => write(MODE_KEY, mode);

/**
 * The demo session, preloaded on first use.
 *
 * Seeding is skipped when the demo session already holds its symbols, so
 * re-entering the demo is instant and does not re-add anything.
 */
export async function demoSession() {
  const token = await sessionFor(DEMO_KEY);
  const { count } = await request("/api/watchlist", { token });
  if (count < DEMO_SYMBOLS.length) {
    await Promise.all(
      DEMO_SYMBOLS.map((symbol) =>
        request("/api/watchlist", {
          token,
          method: "POST",
          body: { symbol, anchor_sessions_ago: 1 },
        }).catch(() => null),
      ),
    );
  }
  return token;
}

export const api = {
  brief: (token) => request("/api/brief", { token }),
  detail: (token, symbol) => request(`/api/symbols/${symbol}/detail`, { token }),
  watchlist: (token) => request("/api/watchlist", { token }),
  symbols: (token, q) =>
    request(`/api/symbols${q ? `?q=${encodeURIComponent(q)}` : ""}`, { token }),
  add: (token, symbol) =>
    request("/api/watchlist", { token, method: "POST", body: { symbol } }),
  remove: (token, symbol) =>
    request(`/api/watchlist/${symbol}`, { token, method: "DELETE" }),
  acknowledge: (token, symbol, snapshotId) =>
    request("/api/brief/ack", {
      token,
      method: "POST",
      body: { symbol, snapshot_id: snapshotId },
    }),
  scenarios: (token) => request("/api/dev/scenarios", { token }),
  simulation: (token) => request("/api/dev/simulate", { token }),
  simulate: (token, body, mode) =>
    request(`/api/dev/simulate${mode ? `?mode=${mode}` : ""}`, {
      token,
      method: "POST",
      body: body || {},
    }),
  resetSimulation: (token) =>
    request("/api/dev/simulate", { token, method: "DELETE" }),
};
