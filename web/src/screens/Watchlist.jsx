import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ErrorState, Loading } from "../components/ui.jsx";

/**
 * Search, add, remove. Nothing else belongs on this screen.
 *
 * The fixture universe is sixteen issuers and is filtered in the browser. The
 * live one is every NSE equity, so searching it is the server's job and the
 * query is debounced rather than sent per keystroke.
 */
export default function Watchlist({ token, live, onDone, onChanged }) {
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

  const refresh = useCallback(
    async (term = "") => {
      try {
        const [, list] = await Promise.all([loadCatalogue(term), api.watchlist(token)]);
        setWatched(new Set(list.items.map((item) => item.symbol)));
      } catch (caught) {
        setError(caught);
      }
    },
    [loadCatalogue, token],
  );

  useEffect(() => {
    setCatalogue(null);
    refresh("");
  }, [refresh]);

  useEffect(() => {
    if (!live) return undefined;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        await loadCatalogue(query);
      } catch (caught) {
        setError(caught);
      } finally {
        setSearching(false);
      }
    }, 300);
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

  async function toggle(symbol, isWatched) {
    setPending(symbol);
    setError(null);
    try {
      if (isWatched) await api.remove(token, symbol);
      else await api.add(token, symbol);
      const list = await api.watchlist(token);
      setWatched(new Set(list.items.map((item) => item.symbol)));
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(null);
    }
  }

  if (error && !catalogue) return <ErrorState error={error} onRetry={() => refresh(query)} />;
  if (!catalogue || !watched) return <Loading lines={5} />;

  return (
    <div>
      <h1 className="display">Your watchlist</h1>
      <p className="muted">
        {watched.size} {watched.size === 1 ? "stock" : "stocks"} followed
        {live ? " · live market data" : " · sample data"}.
      </p>

      <input
        className="search"
        type="search"
        placeholder={live ? "Search NSE by name or ticker" : "Search by name or ticker"}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-label="Search symbols"
      />

      {error && (
        <p className="small" style={{ color: "var(--amber)" }}>
          {error.message}
        </p>
      )}

      {searching && <p className="small muted">Searching…</p>}

      {results.length === 0 && !searching && (
        <p className="state">
          {query ? `Nothing matches “${query}”.` : "Nothing to show."}
        </p>
      )}

      <div style={{ marginTop: "1rem" }}>
        {results.map((row) => {
          const isWatched = watched.has(row.symbol);
          return (
            <div className="manage-row" key={row.symbol}>
              <span className="ticker">{row.symbol}</span>
              <span className="company">{row.name}</span>
              <button
                className="action"
                onClick={() => toggle(row.symbol, isWatched)}
                disabled={pending === row.symbol}
              >
                {pending === row.symbol ? "…" : isWatched ? "Remove" : "Add"}
              </button>
            </div>
          );
        })}
      </div>

      {watched.size > 0 && (
        <p style={{ marginTop: "2.5rem" }}>
          <button className="action" onClick={onDone}>
            Go to the brief
          </button>
        </p>
      )}
    </div>
  );
}
