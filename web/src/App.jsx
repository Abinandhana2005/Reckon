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
 * Two identities, one product.
 *
 * `mode` decides which session token every request carries. Demo Mode is a
 * different guest user on the server, so entering it cannot add a symbol to the
 * real watchlist, move a real anchor, or leave a simulation behind on the real
 * session. Leaving is just a switch back.
 */

const DEMO_DEFAULT = "market-wide";

export default function App() {
  const [path, go] = usePath();
  const [mode, setMode] = useState(null); // null while bootstrapping
  const [tokens, setTokens] = useState({ real: null, demo: null });
  const [needsOnboarding, setNeedsOnboarding] = useState(false);
  const [busy, setBusy] = useState(null);
  const [fatal, setFatal] = useState(null);

  const [brief, setBrief] = useState(null);
  const [briefError, setBriefError] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);

  const [scenarios, setScenarios] = useState([]);
  const [demoState, setDemoState] = useState({ scenario: DEMO_DEFAULT, stale: false });

  const token = mode === "demo" ? tokens.demo : tokens.real;
  const symbol = path.startsWith("/s/") ? decodeURIComponent(path.slice(3)) : null;

  /* ---------- bootstrap ---------- */

  useEffect(() => {
    (async () => {
      try {
        const real = await realSession();
        setTokens((current) => ({ ...current, real }));
        const { count } = await api.watchlist(real);
        setNeedsOnboarding(count === 0);

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
    const onboarding = needsOnboarding && mode !== "demo";
    if (!token || onboarding || symbol || path === "/watchlist") return;
    loadBrief();
  }, [token, needsOnboarding, mode, symbol, path, loadBrief]);

  useEffect(() => {
    if (!token || !symbol) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    api
      .detail(token, symbol)
      .then((found) => !cancelled && setDetail(found))
      .catch((error) => !cancelled && setDetailError(error));
    return () => {
      cancelled = true;
    };
  }, [token, symbol]);

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
      if (demoState.stale) {
        await api.simulate(tokens.demo, {
          scenario: demoState.scenario,
          anchor_sessions_ago: 1,
        });
        setDemoState((current) => ({ ...current, stale: false }));
      } else {
        await api.simulate(
          tokens.demo,
          { scenario: demoState.scenario, anchor_sessions_ago: 1 },
          "stale",
        );
        setDemoState((current) => ({ ...current, stale: true }));
      }
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
      setNeedsOnboarding(count === 0);
    } catch {
      setNeedsOnboarding(true);
    }
    go("/");
  }

  /* ---------- render ---------- */

  if (fatal) {
    return (
      <Screen>
        <ErrorState error={fatal} onRetry={() => window.location.reload()} />
      </Screen>
    );
  }

  if (!mode || !token) {
    return (
      <Screen>
        <Loading lines={4} />
      </Screen>
    );
  }

  const inDemo = mode === "demo";

  if (needsOnboarding && !inDemo && path === "/") {
    return (
      <Screen bare>
        <Welcome
          busy={busy}
          onDemo={enterDemo}
          onBuild={() => {
            setNeedsOnboarding(false);
            go("/watchlist");
          }}
        />
      </Screen>
    );
  }

  return (
    <Screen
      go={go}
      path={path}
      inDemo={inDemo}
      demo={
        inDemo && (
          <DemoBar
            available={scenarios}
            active={demoState}
            busy={busy === "scenario"}
            onScenario={pickScenario}
            onStale={toggleStale}
            onLeave={leaveDemo}
          />
        )
      }
    >
      {symbol ? (
        detailError ? (
          <ErrorState error={detailError} onRetry={() => go("/")} />
        ) : detail ? (
          <Detail detail={detail} onBack={() => go("/")} />
        ) : (
          <Loading lines={4} />
        )
      ) : path === "/watchlist" ? (
        <Watchlist
          token={token}
          onDone={() => go("/")}
          onChanged={() => {
            setBrief(null);
            setNeedsOnboarding(false);
          }}
        />
      ) : briefError ? (
        <ErrorState error={briefError} onRetry={loadBrief} />
      ) : brief ? (
        brief.counts.checked === 0 ? (
          <EmptyWatchlist onManage={() => go("/watchlist")} />
        ) : (
          <Brief brief={brief} onOpen={(code) => go(`/s/${code}`)} />
        )
      ) : (
        <Loading lines={4} />
      )}
    </Screen>
  );
}

function EmptyWatchlist({ onManage }) {
  return (
    <div className="state">
      <p className="display">Nothing to check yet.</p>
      <p>Add the stocks you follow and Reckon will tell you what changed.</p>
      <p style={{ marginTop: "1.5rem" }}>
        <button className="action" onClick={onManage}>
          Build my watchlist
        </button>
      </p>
    </div>
  );
}

function Screen({ children, go, path, inDemo, demo, bare }) {
  return (
    <div className="shell">
      {!bare && (
        <>
          <header className="topbar">
            <div className="wrap topbar-inner">
              <a
                className="wordmark"
                style={{ textDecoration: "none", color: "inherit" }}
                {...(go ? link(go, "/") : { href: "/" })}
              >
                Reckon
              </a>
              {go && !inDemo && (
                <nav className="navlinks">
                  <a
                    className="navlink"
                    aria-current={path === "/" ? "page" : undefined}
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
                </nav>
              )}
            </div>
          </header>
          {demo}
        </>
      )}
      <main>
        <div className="wrap">{children}</div>
      </main>
    </div>
  );
}
