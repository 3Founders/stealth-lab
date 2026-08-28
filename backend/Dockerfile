# Image for the StealthLab MCP server (demo.md C1's "backend service").
#
# BUILD CONTEXT MUST BE THE REPO ROOT, not this directory -- see
# .dockerignore's header comment for why: app/mcp_server/server.py imports
# Agent/RepoSandbox from experiments/swebench_pro/ as a real sibling of
# backend/ at module import time (README_MCP_SERVER.md setup step #3).
# docker-compose.yml builds this with `context: .` + `dockerfile:
# backend/Dockerfile`; building it standalone requires the same:
#   docker build -f backend/Dockerfile -t stealthlab-backend .
# (note the trailing `.` -- repo root, not `backend/.`)

FROM python:3.12-slim

# None of backend/requirements.txt's pins need a compiler on standard
# linux/amd64 or linux/arm64 (z3-solver, numpy, scipy, pdfplumber,
# tree-sitter-language-pack all ship prebuilt wheels as of this pin) --
# add build-essential here first if a future dependency needs one.
WORKDIR /repo

# git is a RUNTIME dependency, not a build convenience. solve_task seeds
# its structural-retrieval tier from `git diff --name-only HEAD` against
# the caller's repo_path. That call is try/except-guarded, so without git
# it does not crash -- it silently returns seed_files=[], and one of the
# tool's two independent retrieval tiers is dead with no error anywhere.
# Confirmed missing from this image on 2026-08-28 while proving the core
# loop; the failure is invisible precisely because it degrades politely.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Both trees, preserving the sibling relationship the import above
# requires -- copying backend/ alone (a backend-scoped context) would
# leave that import unresolved and the container would crash on boot.
COPY backend/ backend/
COPY experiments/swebench_pro/ experiments/swebench_pro/

WORKDIR /repo/backend

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD python scripts/docker_healthcheck.py

# Migrate then serve, exactly what backend/README_MCP_SERVER.md documents
# running bare-metal (`uvicorn app.mcp_server.server:app`), except
# --host 0.0.0.0 instead of 127.0.0.1 -- see docker-compose.yml's comment
# on why that's still loopback-only in effect from the host's side.
# Migration failure must stop the container, not silently serve against a
# half-migrated database -- `&&`, not `;`.
CMD ["sh", "-c", "python scripts/migrate.py && exec uvicorn app.mcp_server.server:app --host 0.0.0.0 --port 8765"]
