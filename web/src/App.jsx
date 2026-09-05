import React, { useCallback, useEffect, useState } from "react";
import { api, demoSession, realSession, rememberMode, storedMode } from "./api.js";
import DemoBar from "./components/DemoBar.jsx";
import { ErrorState, Loading } from "./components/ui.jsx";
import { link, usePath } from "./router.js";
import Brief from "./screens/Brief.jsx";
import Detail from "./screens/Detail.jsx";
import Watchlist from "./screens/Watchlist.jsx";
import Welcome from "./screens/Welcome.jsx";

/**
 * Two identities, one product, and a hierarchy the reader can hold in their head.
 *
 * `mode` decides which session token every request carries. The demo is a
 * different guest user on the server, so entering it cannot add a symbol to the
 * real watchlist, move a real anchor, or leave a simulation behind on the real
 * session. Leaving is just a switch back.
 *
 * There are two places to be — the Brief and the Watchlist — plus the demo,
 * which is a showcase rather than a third place. Live and Sample are not
 * destinations at all: they are which data the watchlist is built from, so the
 * choice lives inside the Watchlist rather than in the navigation.
 */

const DEMO_DEFAULT = "market-wide";
const THEME_KEY = "reckon.theme";

/** The symbol the URL is on right now, read at the moment a response lands. */
function currentSymbol() {
  const path = window.location.pathname;
  return path.startsWith("/s/") ? decodeURIComponent(path.slice(3)) : null;
}

function storedTheme() {
  try {
    return window.localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}

export default function App() {
  const [path, go] = usePath();
  const [mode, setMode] = useState(null); // null while bootstrapping
  const [tokens, setTokens] = useState({ real: null, demo: null });
  const [onboarding, setOnboarding] = useState(null); // null | "intro" | "source"
  const [busy, setBusy] = useState(null);
  const [fatal, setFatal] = useState(null);
  const [theme, setTheme] = useState(storedTheme);

  const [brief, setBrief] = useState(null);
  const [briefError, setBriefError] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);

  const [dataMode, setDataMode] = useState(null);
  const [sourceError, setSourceError] = useState(null);
  const [scenarios, setScenarios] = useState([]);
  const [demoState, setDemoState] = useState({ scenario: DEMO_DEFAULT, stale: false });

  const token = mode === "demo" ? tokens.demo : tokens.real;
  const symbol = path.startsWith("/s/") ? decodeURIComponent(path.slice(3)) : null;

  /* ---------- theme ---------- */

  // An explicit choice wins; with none stored the OS preference is honoured by
  // the stylesheet's own media query, so nothing is stamped on the root.
  useEffect(() => {
    const root = document.documentElement;
    if (theme) root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }, [theme]);

  function toggleTheme() {
    const prefersDark =
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const current = theme || (prefersDark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* Private browsing. The choice still holds for this page load. */
    }
  }

  const themeLabel = (() => {
    const prefersDark =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    const current = theme || (prefersDark ? "dark" : "light");
    return current === "dark" ? "Light" : "Dark";
  })();

  /* ---------- bootstrap ---------- */

  useEffect(() => {
    (async () => {
      try {
        const real = await realSession();
        setTokens((current) => ({ ...current, real }));
        const { count } = await api.watchlist(real);
        setOnboarding(count === 0 ? "intro" : null);
        api.mode(real).then(setDataMode).catch(() => setDataMode(null));

        // A refresh in the middle of a walkthrough should not drop out of the
        // demo. Which scenario is running is read back from the server, which
        // is where it lives, rather than remembered in the browser.
        if (storedMode() === "demo") {
          const demo = await demoSession();
          const state = await api.simulation(demo);
          setTokens((current) => ({ ...current, demo }));
          setDemoState({
            scenario: state.scenario || DEMO_DEFAULT,
            stale: Object.keys(state.freshness || {}).length > 0,
          });
          loadScenarioLabels(demo);
          setMode("demo");
          return;
        }
        setMode("real");
      } catch (error) {
        setFatal(error);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function loadScenarioLabels(demo) {
    api
      .scenarios(demo)
      .then((body) => setScenarios(body.scenarios.map((entry) => entry.label)))
      .catch(() => setScenarios([]));
  }

  /* ---------- data ---------- */

  const loadBrief = useCallback(async () => {
    if (!token) return;
    setBriefError(null);
    try {
      setBrief(await api.brief(token));
    } catch (error) {
      setBriefError(error);
    }
  }, [token]);

  useEffect(() => {
    // Onboarding is a fact about the real watchlist only. The demo session has
    // its own symbols, so an empty real watchlist must not hold its brief back.
    const blocked = onboarding !== null && mode !== "demo";
    // `mode` decides which session the request carries, and it settles after
    // `token` does. Loading before then fetched the brief twice on every page
    // load -- and a brief read is a write, so the second one moved state.
    if (!mode || !token || blocked || symbol || path === "/watchlist") return;
    loadBrief();
  }, [token, onboarding, mode, symbol, path, loadBrief]);

  /**
   * Load one symbol's detail.
   *
   * `wanted` is compared against the symbol in the URL when the response
   * arrives: opening two symbols quickly can settle out of order, and the
   * slower of the two would otherwise paint over the one being read.
   */
  const loadDetail = useCallback(
    async (wanted = symbol) => {
      if (!token || !wanted) return;
      setDetailError(null);
      try {
        const found = await api.detail(token, wanted);
        if (currentSymbol() === wanted) setDetail(found);
      } catch (error) {
        if (currentSymbol() === wanted) setDetailError(error);
      }
    },
    [token, symbol],
  );

  useEffect(() => {
    if (!token || !symbol) {
      setDetail(null);
      return;
    }
    setDetail(null);
    loadDetail(symbol);
  }, [token, symbol, loadDetail]);

  /* ---------- acknowledgement ---------- */

  /**
   * Marking one symbol as seen.
   *
   * The snapshot id travels back to the server, which refuses to advance the
   * anchor past it. A change that arrived between render and tap therefore
   * stays unread rather than being marked seen by a tap that never saw it.
   */
  async function acknowledge(code, snapshotId) {
    setBusy(`ack:${code}`);
    try {
      await api.acknowledge(token, code, snapshotId);
      setBrief(null);
      if (symbol) await loadDetail(symbol);
      else await loadBrief();
    } catch (error) {
      setBriefError(error);
    } finally {
      setBusy(null);
    }
  }

  /* ---------- demo ---------- */

  async function enterDemo() {
    setBusy("demo");
    try {
      const demo = tokens.demo || (await demoSession());
      setTokens((current) => ({ ...current, demo }));
      await api.simulate(demo, { scenario: DEMO_DEFAULT, anchor_sessions_ago: 1 });
      setDemoState({ scenario: DEMO_DEFAULT, stale: false });
      loadScenarioLabels(demo);
      setBrief(null);
      rememberMode("demo");
      setMode("demo");
      setOnboarding(null);
      go("/");
    } catch (error) {
      setFatal(error);
    } finally {
      setBusy(null);
    }
  }

  async function pickScenario(key) {
    setBusy("scenario");
    try {
      await api.simulate(tokens.demo, { scenario: key, anchor_sessions_ago: 1 });
      setDemoState({ scenario: key, stale: false });
      setBrief(null);
      if (symbol) go("/");
      else await loadBrief();
    } catch (error) {
      setBriefError(error);
    } finally {
      setBusy(null);
    }
  }

  async function toggleStale() {
    setBusy("scenario");
    try {
      const next = !demoState.stale;
      await api.simulate(
        tokens.demo,
        { scenario: demoState.scenario, anchor_sessions_ago: 1 },
        next ? "stale" : undefined,
      );
      setDemoState((current) => ({ ...current, stale: next }));
      setBrief(null);
      if (symbol) go("/");
      else await loadBrief();
    } catch (error) {
      setBriefError(error);
    } finally {
      setBusy(null);
    }
  }

  async function leaveDemo() {
    setBrief(null);
    rememberMode("real");
    setMode("real");
    try {
      const { count } = await api.watchlist(tokens.real);
      setOnboarding(count === 0 ? "intro" : null);
    } catch {
      setOnboarding("intro");
    }
    go("/");
  }

  /* ---------- data source ---------- */

  /**
   * Change which data the watchlist is built from.
   *
   * This navigates nowhere. Live and Sample are not routes -- they are what the
   * current screen is about -- so switching from the Watchlist has to leave the
   * reader on the Watchlist, looking at the other source's instruments. The one
   * caller that does want to move afterwards is onboarding, and it says so
   * itself.
   */
  async function switchDataMode(next) {
    setBusy("mode");
    setSourceError(null);
    try {
      setDataMode(await api.setMode(tokens.real, next));
      // The two sources hold different instruments, so what was on screen for
      // one says nothing about the other.
      setBrief(null);
      setDetail(null);
      setOnboarding(null);
    } catch (error) {
      // Surfaced where the switch was made. Routing it to the brief's error
      // slot left the Watchlist silently showing the previous source.
      setSourceError(error);
    } finally {
      setBusy(null);
    }
  }

  async function refreshLive() {
    setBusy("mode");
    try {
      await api.refreshLive(tokens.real);
      setBrief(null);
      if (symbol) await loadDetail(symbol);
      else await loadBrief();
    } catch (error) {
      setBriefError(error);
    } finally {
      setBusy(null);
    }
  }

  /* ---------- render ---------- */

  if (fatal) {
    return (
      <Shell>
        <div className="wrap">
          <ErrorState error={fatal} onRetry={() => window.location.reload()} />
        </div>
      </Shell>
    );
  }

  if (!mode || !token) {
    return (
      <Shell>
        <div className="wrap">
          <Loading note="Starting Reckon…" />
        </div>
      </Shell>
    );
  }

  const inDemo = mode === "demo";
  const source = inDemo ? { mode: "replay", demo: true } : dataMode;

  if (onboarding && !inDemo && path === "/") {
    return (
      <div className="shell">
        <Welcome
          step={onboarding}
          busy={busy}
          live={dataMode}
          themeLabel={themeLabel}
          onToggleTheme={toggleTheme}
          onDemo={enterDemo}
          onBuild={() => setOnboarding("source")}
          onBack={() => setOnboarding("intro")}
          onChoose={async (picked) => {
            await switchDataMode(picked);
            go("/watchlist");
          }}
        />
      </div>
    );
  }

  return (
    <Shell
      go={go}
      path={path}
      inDemo={inDemo}
      themeLabel={themeLabel}
      onToggleTheme={toggleTheme}
      onEnterDemo={enterDemo}
    >
      {inDemo && (
        <DemoBar
          available={scenarios}
          active={demoState}
          busy={busy === "scenario"}
          onScenario={pickScenario}
          onStale={toggleStale}
          onLeave={leaveDemo}
        />
      )}

      {symbol ? (
        detailError ? (
          <div className="wrap">
            <ErrorState error={detailError} onRetry={() => go("/")} />
          </div>
        ) : detail ? (
          <Detail
            detail={detail}
            busy={busy}
            onBack={() => go("/")}
            onAcknowledge={inDemo ? null : acknowledge}
            onRefresh={!inDemo && dataMode?.mode === "live" ? refreshLive : null}
          />
        ) : (
          <div className="wrap">
            <Loading note="Opening the evidence…" />
          </div>
        )
      ) : path === "/watchlist" ? (
        source === null ? (
          // Which source this session is on is server state. Rendering before
          // it arrives listed the sample issuers for a moment to a reader who
          // is on live data.
          <div className="wrap">
            <Loading note="Loading your watchlist…" />
          </div>
        ) : (
          <Watchlist
            token={token}
            mode={source}
            busy={busy}
            error={sourceError}
            onSwitchSource={inDemo ? null : switchDataMode}
            onRefresh={inDemo ? null : refreshLive}
            onDone={() => go("/")}
            onChanged={() => {
              setBrief(null);
              setOnboarding(null);
            }}
          />
        )
      ) : briefError ? (
        <div className="wrap">
          <ErrorState error={briefError} onRetry={loadBrief} />
        </div>
      ) : brief ? (
        brief.counts.checked === 0 ? (
          <EmptyWatchlist onManage={() => go("/watchlist")} />
        ) : (
          <Brief
            brief={brief}
            busy={busy}
            onOpen={(code) => go(`/s/${code}`)}
            onViewEverything={() => go("/watchlist")}
            onAcknowledge={inDemo ? null : acknowledge}
          />
        )
      ) : (
        <div className="wrap">
          <Loading note="Checking your watchlist…" />
        </div>
      )}
    </Shell>
  );
}

function EmptyWatchlist({ onManage }) {
  return (
    <div className="wrap">
      <div className="state">
        <p className="eyebrow">Reckon brief</p>
        <h1 className="state-title" style={{ marginTop: "20px" }}>
          Nothing to check yet.
        </h1>
        <p className="state-body">
          Add the stocks you follow and Reckon will account for every one of
          them, every time you come back.
        </p>
        <button className="btn" onClick={onManage}>
          Build my watchlist
        </button>
      </div>
    </div>
  );
}

/**
 * The frame.
 *
 * The header is the only global chrome: two destinations named for what they
 * answer -- the Brief is what changed, the Watchlist is what Reckon checks --
 * plus the demo, which sits apart because it is a showcase over separate data.
 * In the demo the nav is suppressed and the demo band owns the exit.
 */
function Shell({ children, go, path, inDemo, themeLabel, onToggleTheme, onEnterDemo }) {
  const onBrief = path === "/" || (path || "").startsWith("/s/");
  return (
    <div className="shell">
      <header className="topbar">
        <div className="wrap topbar-inner">
          <a className="wordmark" {...(go ? link(go, "/") : { href: "/" })}>
            RECKON
          </a>
          {go && !inDemo && (
            <nav className="navlinks" aria-label="Main">
              <a
                className="navlink"
                aria-current={onBrief ? "page" : undefined}
                {...link(go, "/")}
              >
                Brief
              </a>
              <a
                className="navlink"
                aria-current={path === "/watchlist" ? "page" : undefined}
                {...link(go, "/watchlist")}
              >
                Watchlist
              </a>
              {onEnterDemo && (
                <button className="navlink" onClick={onEnterDemo}>
                  Demo
                </button>
              )}
            </nav>
          )}
          <div className="topbar-end">
            {onToggleTheme && (
              <button className="theme-toggle" onClick={onToggleTheme}>
                {themeLabel}
              </button>
            )}
          </div>
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}
