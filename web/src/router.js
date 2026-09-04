import { useCallback, useEffect, useState } from "react";

/**
 * Three screens do not need a routing library.
 *
 * The server already serves index.html for unknown paths, so real URLs work on
 * refresh and the back button behaves.
 */
export function usePath() {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((next) => {
    if (next === window.location.pathname) return;
    window.history.pushState({}, "", next);
    setPath(next);
    window.scrollTo(0, 0);
  }, []);

  return [path, go];
}

export function link(go, href) {
  return {
    href,
    onClick: (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      go(href);
    },
  };
}
