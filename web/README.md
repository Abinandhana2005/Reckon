# Frontend

The React (Vite) app lives here.

FastAPI serves the production build from `web/dist` on the same origin as the
API, so there is no CORS configuration and one deployable. `app/api/main.py`
mounts it only when `web/dist/index.html` exists, which is why the backend runs
before this directory holds anything.

Two things the build has to honour:

- output to `web/dist` (Vite's default `outDir`)
- unknown paths fall back to `index.html`, so client-side routes survive a
  refresh; the server already does this, the router just has to expect it

`RECKON_WEB_DIST` overrides the location if a deployment needs it.
