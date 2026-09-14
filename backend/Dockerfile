# Image for the StealthLab MCP server (demo.md C1's "backend service").
#
# Build context is still the REPO ROOT for now (docker-compose.yml builds
# this with `context: .` + `dockerfile: backend/Dockerfile`; building it
# standalone: `docker build -f backend/Dockerfile -t stealthlab-backend .`,
# trailing `.` = repo root). This is no longer STRICTLY required the way it
# used to be -- app/mcp_server/server.py's Agent/RepoSandbox import now
# resolves entirely inside backend/ (app/execution/coding_agent.py, moved
# from experiments/swebench_pro/agent.py specifically so backend/ has zero
# runtime dependency on the sibling experiments/ tree). A backend-only
# build context is a real, safe follow-up simplification now, just not
# made in this pass (untested against an actual docker build in this
# environment).

FROM python:3.12-slim

# None of backend/requirements.txt's pins need a compiler on standard
# linux/amd64 or linux/arm64 (z3-solver, numpy, scipy, pdfplumber,
# tree-sitter-language-pack all ship prebuilt wheels as of this pin) --
# add build-essential here first if a future dependency needs one.
WORKDIR /repo

# git is a RUNTIME dependency, not a build convenience. find_best_way seeds
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

COPY backend/ backend/

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
