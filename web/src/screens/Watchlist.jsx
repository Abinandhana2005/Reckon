import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ErrorState, Loading } from "../components/ui.jsx";

/** Search, add, remove. Nothing else belongs on this screen. */
export default function Watchlist({ token, onDone, onChanged }) {
  const [query, setQuery] = useState("");
  const [catalogue, setCatalogue] = useState(null);
  const [watched, setWatched] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(null);

  async function refresh() {
    try {
      const [symbols, list] = await Promise.all([
        api.symbols(token),
        api.watchlist(token),
      ]);
      setCatalogue(symbols.symbols);
      setWatched(new Set(list.items.map((item) => item.symbol)));
    } catch (caught) {
      setError(caught);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const results = useMemo(() => {
    if (!catalogue) return [];
    const needle = query.trim().toLowerCase();
    if (!needle) return catalogue;
    return catalogue.filter(
      (row) =>
        row.symbol.toLowerCase().includes(needle) ||
        row.name.toLowerCase().includes(needle),
    );
  }, [catalogue, query]);

  async function toggle(symbol, isWatched) {
    setPending(symbol);
    try {
      if (isWatched) await api.remove(token, symbol);
      else await api.add(token, symbol);
      await refresh();
      onChanged?.();
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(null);
    }
  }

  if (error) return <ErrorState error={error} onRetry={refresh} />;
  if (!catalogue || !watched) return <Loading lines={5} />;

  return (
    <div>
      <h1 className="display">Your watchlist</h1>
      <p className="muted">
        {watched.size} {watched.size === 1 ? "stock" : "stocks"} followed.
      </p>

      <input
        className="search"
        type="search"
        placeholder="Search by name or ticker"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-label="Search symbols"
      />

      {results.length === 0 && (
        <p className="state">Nothing matches “{query}”.</p>
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
