import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ErrorState, Loading, SectionHead } from "../components/ui.jsx";

/**
 * What Reckon checks, and where those prices come from.
 *
 * Order matters here: title, then source, then search, then what is already
 * followed. The data source belongs on this screen rather than in the
 * navigation -- Live and Sample are not places to go, they are what the
 * watchlist is made of. Each keeps its own instruments, so switching is a change
 * of subject and the list below reloads entirely.
 *
 * Sample search filters sixteen prepared issuers in the browser. Live search is
 * provider-backed and debounced, so typing does not open a request per
 * keystroke.
 */
const DEBOUNCE_MS = 300;

export default function Watchlist({
  token,
  mode,
  busy,
  error: sourceError,
  onSwitchSource,
  onRefresh,
  onDone,
  onChanged,
}) {
  const live = mode && mode.mode === "live";
  const [query, setQuery] = useState("");
  const [catalogue, setCatalogue] = useState(null);
  const [watched, setWatched] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(null);
  const [searching, setSearching] = useState(false);

  const loadCatalogue = useCallback(
    async (term) => {
      const body = await api.symbols(token, live ? term : undefined);
      setCatalogue(body.symbols);
    },
    [token, live],
  );

  const reloadWatched = useCallback(async () => {
    const list = await api.watchlist(token);
    setWatched(list.items);
  }, [token]);

  useEffect(() => {
    setCatalogue(null);
    setWatched(null);
    setQuery("");
    (async () => {
      try {
        await Promise.all([loadCatalogue(""), reloadWatched()]);
      } catch (caught) {
        setError(caught);
      }
    })();
  }, [loadCatalogue, reloadWatched]);

  useEffect(() => {
    if (!live) return undefined;
    // An empty live search would ask the provider for nothing in particular.
    if (!query.trim()) {
      setSearching(false);
      setCatalogue([]);
      return undefined;
    }
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        setError(null);
        await loadCatalogue(query);
      } catch (caught) {
        setError(caught);
      } finally {
        setSearching(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, live, loadCatalogue]);

  const results = useMemo(() => {
    if (!catalogue) return [];
    if (live) return catalogue;
    const needle = query.trim().toLowerCase();
    if (!needle) return catalogue;
    return catalogue.filter(
      (row) =>
        row.symbol.toLowerCase().includes(needle) ||
        row.name.toLowerCase().includes(needle),
    );
  }, [catalogue, query, live]);

  const followed = useMemo(
    () => new Set((watched || []).map((item) => item.symbol)),
    [watched],
  );

  async function toggle(symbol, isWatched) {
    setPending(symbol);
    setError(null);
    try {
      if (isWatched) await api.remove(token, symbol);
      else await api.add(token, symbol);
      await reloadWatched();
      if (onChanged) onChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(null);
    }
  }

  if (error && !catalogue) {
    return (
      <div className="wrap">
        <ErrorState error={error} onRetry={() => window.location.reload()} />
      </div>
    );
  }
  if (!catalogue || !watched) {
    return (
      <div className="wrap">
        <Loading note="Loading your watchlist…" />
      </div>
    );
  }

  const count = watched.length;
  const intro = count
    ? count +
      (count === 1 ? " stock followed on " : " stocks followed on ") +
      (live ? "live data" : "sample data") +
      ". Every one of them is accounted for in the brief, whether or not it moved."
    : "Nothing followed yet. Search below and add the stocks you want Reckon to check.";

  return (
    <div className="wrap page">
      <header className="watch-head">
        <div className="watch-head-main">
          <p className="eyebrow" style={{ marginBottom: "20px" }}>
            Watchlist
          </p>
          <h1 className="watch-title">My watchlist</h1>
          <p className="watch-intro">{intro}</p>
        </div>
        <div className="watch-count">
          <div className="watch-count-n tabular">{count}</div>
          <div className="watch-count-label">Stocks followed</div>
        </div>
      </header>

      {onSwitchSource && mode && (
        <section className="block">
          <SectionHead
            title="Data source"
            gloss="Each source keeps its own watchlist"
          />
          {sourceError && <p className="inline-error">{sourceError.message}</p>}
          {mode.live_available ? (
            <div className="sources">
              <button
                className="source"
                aria-pressed={Boolean(live)}
                disabled={busy === "mode"}
                onClick={() => onSwitchSource("live")}
              >
                <span className="source-top">
                  <span className="source-dot" />
                  <span className="source-title">Live data</span>
                  <span className="source-state">
                    {live ? "Selected" : "Available"}
                  </span>
                </span>
                <span className="source-blurb">
                  Real NSE market data via Yahoo Finance
                </span>
              </button>
              <button
                className="source"
                aria-pressed={!live}
                disabled={busy === "mode"}
                onClick={() => onSwitchSource("replay")}
              >
                <span className="source-top">
                  <span className="source-dot" />
                  <span className="source-title">Sample data</span>
                  <span className="source-state">
                    {live ? "Available" : "Selected"}
                  </span>
                </span>
                <span className="source-blurb">Deterministic sample market data</span>
              </button>
            </div>
          ) : (
            <p className="empty-note">
              Live market data is off on this server ({mode.live_reason}). The
              watchlist below runs on sample data.
            </p>
          )}
          {live && onRefresh && (
            <p style={{ marginTop: "16px" }}>
              <button
                className="linkish"
                onClick={onRefresh}
                disabled={busy === "mode"}
              >
                {busy === "mode" ? "Refreshing…" : "Refresh prices"}
              </button>
            </p>
          )}
        </section>
      )}

      <section className="block">
        <SectionHead
          title={live ? "Search NSE" : "Add from sample data"}
          gloss={
            live
              ? "Real NSE equities, resolved through Yahoo Finance"
              : "Sixteen prepared issuers — nothing here is a real company"
          }
        />
        <div className="searchbar" style={{ marginTop: "16px" }}>
          <span className="searchbar-slash" aria-hidden="true">
            /
          </span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={
              live
                ? "Search stocks or companies — TCS, Reliance, Infosys"
                : "Search by name or ticker"
            }
            aria-label="Search symbols"
          />
          {searching && <span className="searchbar-status">Searching…</span>}
        </div>

        {error && <p className="inline-error">{error.message}</p>}

        {results.length > 0 && (
          <div style={{ marginTop: "4px" }}>
            {results.map((row) => {
              const isWatched = followed.has(row.symbol);
              return (
                <div className="listrow" key={row.symbol}>
                  <span className="listrow-ticker">{row.symbol}</span>
                  <span className="listrow-name">{row.name}</span>
                  {isWatched ? (
                    <span className="added">Added</span>
                  ) : (
                    <button
                      className="btn btn-small"
                      onClick={() => toggle(row.symbol, false)}
                      disabled={pending === row.symbol}
                    >
                      {pending === row.symbol ? "Adding…" : "Add"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {results.length === 0 && !searching && (
          <p className="empty-note">
            {query
              ? "Nothing matched “" +
                query +
                "”." +
                (live ? " Try the exchange ticker, such as TCS or RELIANCE." : "")
              : live
                ? "Type a ticker or a company name to search."
                : "Nothing to show."}
          </p>
        )}
      </section>

      <section className="block-wide">
        <SectionHead title="Followed" count={count} />
        {count > 0 ? (
          <div className="rows">
            {watched.map((row) => (
              <div className="listrow followed" key={row.symbol}>
                <span className="listrow-ticker">{row.symbol}</span>
                <span className="listrow-name">{row.name}</span>
                {row.sector && <span className="listrow-sector">{row.sector}</span>}
                <button
                  className="btn-quiet"
                  onClick={() => toggle(row.symbol, true)}
                  disabled={pending === row.symbol}
                >
                  {pending === row.symbol ? "…" : "Remove"}
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="empty-note">
            Nothing followed on this source yet. Search above and add the stocks
            you want Reckon to check.
          </p>
        )}
      </section>

      {count > 0 && (
        <div style={{ marginTop: "56px" }}>
          <button className="btn" onClick={onDone}>
            Go to the brief &rarr;
          </button>
        </div>
      )}
    </div>
  );
}
