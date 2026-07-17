# Deploying the Agentic Harness API server

The harness runs as an HTTP API (`interfaces/server.py`, FastAPI, D34) with
per-request JWT auth and per-user isolation. This document covers getting it
into production, and — the fiddly part — running the command **sandbox**
(`HARNESS_SANDBOX=docker`) when the API itself is containerized.

## TL;DR

```bash
cp .env.example .env          # set HARNESS_API_KEY, HARNESS_JWT_SECRET, POSTGRES_PASSWORD
docker compose up --build     # API + Postgres, sandbox OFF (safe default)
```

To also enable the containerized command sandbox, add the override:

```bash
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up --build
```

## What each file gives you

| File | Purpose |
|---|---|
| `Dockerfile` | The API image (includes the `docker` CLI client so the sandbox can drive an external daemon). |
| `docker-compose.yml` | API + Postgres. `HARNESS_DB_URL` → Postgres; safe server defaults (`confine_workspace=true`, `allowlist`). Sandbox **off**. |
| `docker-compose.sandbox.yml` | Override adding an isolated Docker-in-Docker sidecar and turning `HARNESS_SANDBOX=docker` on (Option B below). |
| `requirements-server.txt` | `fastapi` + `uvicorn` (install alongside `requirements.txt`). |
| `main_server.py` | `python main_server.py` entry point (equivalent to `uvicorn interfaces.server:app`). |

## Required configuration (production)

Set these as real secrets (not a committed `.env`):

| Var | Why |
|---|---|
| `HARNESS_MODEL` + `HARNESS_API_KEY` | the model and its key — required |
| `HARNESS_JWT_SECRET` | a **stable** 32+ byte secret (`python3 -c "import secrets;print(secrets.token_hex(32))"`). If unset it auto-generates one per host, which invalidates tokens across restarts/replicas. |
| `HARNESS_DB_URL` | `postgresql+psycopg://…` for multi-user. SQLite is single-writer — fine for one process, not for a real server. |
| `HARNESS_PERMISSION_MODE` | `allowlist` — an HTTP turn has no human to answer an `ask` prompt (the server denies `ask`, fail-safe). |
| `HARNESS_CONFINE_WORKSPACE` | `true` — confine file/shell tools to per-session dirs. |

Everything else has sensible defaults (see `.env.example`).

---

## The sandbox and Docker

`engine/sandbox.py` shells out to the `docker` CLI, which needs a reachable
**Docker daemon**. When the API runs as a container, it has no daemon inside
it — so you must give it access to one. Three ways, worst-to-best for
isolation:

### Option A — Mount the host Docker socket (Docker-out-of-Docker)
Simplest. Add to the `api` service:
```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      HARNESS_SANDBOX: "docker"
```
Sandbox containers run on the **host** daemon as siblings.
- ✅ Trivial, fast, no extra service.
- ⚠️ **Grants the API container root-equivalent control of the host** (the
  socket can start a container mounting `/`). This undercuts the sandbox's
  purpose for untrusted input. Use only on a **trusted single-tenant** box.

### Option B — Docker-in-Docker sidecar (recommended) — `docker-compose.sandbox.yml`
The provided override runs a `docker:27-dind` daemon and points the API at it
(`DOCKER_HOST=tcp://dind:2375`). Sandbox containers live **inside dind**, not
on the host.
```bash
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up --build
```
- ✅ The host stays out of reach — a proper isolation boundary.
- ⚠️ The dind service needs `privileged: true`; nested storage/networking is
  a bit slower. Standard trade-off for sandboxed CI/agent workloads.
- ⚠️ **DinD bind-mount gotcha:** a `-v /path:/workspace` mount is resolved on
  the **dind daemon's** filesystem, not the API container's. So the
  per-session workspace must live on a volume mounted into **both** the `api`
  and `dind` services at the **same path**. The override handles this: it
  shares the `harness_ws` volume at `/app/workspaces` in both, and sets
  `HARNESS_WORKSPACE_DIR=/app/workspaces`. If you customize paths, keep them
  identical on both services or the sandbox will see an empty workspace.

  *(Verified this session: pointing the harness `SandboxManager` at a dind
  daemon ran the command inside dind — egress denied, host files unreadable,
  no container on the host. The only failure mode was a workspace mounted on
  a path dind didn't share, which the override prevents.)*

### Option C — Don't containerize the API; run it on a Docker host
Run `uvicorn interfaces.server:app` **directly** on a VM that has Docker. No
nesting — the server talks to the local daemon exactly like the CLI does.
- ✅ Simplest model, best isolation (host daemon, no socket sharing).
- The compose file is then just Postgres.

**Recommendation:** Option B or C for anything untrusted/multi-tenant; Option A
only for a trusted internal deployment.

## Getting Docker on a fresh host

If `docker` isn't installed (Ubuntu/Debian):
```bash
curl -fsSL https://get.docker.com | sh      # installs CLI + daemon
sudo systemctl enable --now docker          # start on boot
sudo usermod -aG docker "$USER"             # run without sudo (re-login)
docker run --rm hello-world                 # verify the daemon works
```
Then set `HARNESS_SANDBOX=docker`. The harness verifies the daemon at startup
and **fails loud** if it's unreachable (it won't silently run unsandboxed).

## Verifying a deployment

```bash
curl -s localhost:8000/health
# register the first account (becomes admin) and get a JWT
curl -s -X POST localhost:8000/auth/register \
  -H 'content-type: application/json' -d '{"username":"admin","password":"…"}'
# use the token
curl -s localhost:8000/sessions -H "authorization: Bearer <token>"
```

## Production checklist / known gaps

- [ ] `HARNESS_JWT_SECRET` set and stable across replicas.
- [ ] `HARNESS_DB_URL` → Postgres (run `scripts/migrate_json_to_db.py` once if upgrading from JSON stores).
- [ ] `HARNESS_PERMISSION_MODE=allowlist`, `HARNESS_CONFINE_WORKSPACE=true`.
- [ ] Sandbox enabled via Option B/C if you run untrusted tasks.
- [ ] TLS terminated in front of the API (a reverse proxy / load balancer) — the app speaks plain HTTP.
- [ ] Persistent volumes for `.harness/` and `workspaces/`.
- **Not yet built:** streaming responses (turns return one JSON blob), human-in-the-loop approval over HTTP (so `ask` mode isn't usable via the API yet), and rate limiting. These are the next milestones (see `VERIFICATION.md` §Phase 5).
