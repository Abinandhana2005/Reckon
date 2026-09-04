# One container: API, classifier, fixture and (once built) the frontend.
#
# Replay mode makes no outbound calls and runs no scheduler, so there is one
# process to start and nothing to coordinate. The image carries no frontend
# build stage yet because web/ holds no app to build; when the Vite project
# lands, a node stage producing web/dist goes above this one and the rest of
# this file is unchanged.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The app writes only to its database, which is Postgres in a deployment, so
# the filesystem can stay read-only to everything but a local SQLite fallback.
RUN useradd --create-home --uid 10001 reckon && chown -R reckon:reckon /srv
USER reckon

EXPOSE 8000

# Railway and Fly both inject PORT; the default keeps `docker run -p 8000:8000`
# working without one.
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
