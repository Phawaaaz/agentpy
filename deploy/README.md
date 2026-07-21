# Deploying the web app

Run the full **web application** — the browser UI your team logs into — on a
server, with PostgreSQL. Everything here is in this `deploy/` folder.

> This is the **web-UI stack** (`server/app.py` + the React frontend). If you
> only want the headless HTTP API with the command sandbox, see the root
> [`DEPLOY.md`](../DEPLOY.md) instead.

---

## What you're deploying

Three containers, one `docker compose` command:

```
  laptop browser ──►  frontend  (nginx: serves the web app + proxies the
  (nothing to                    API — one origin, no CORS)
   install)                 │
                            ▼
                       backend   (FastAPI + the agent harness)
                            │
                            ▼
                       db        (PostgreSQL: users, sessions, usage/cost)
```

Your team installs nothing — they open a browser to the server and log in.
Accounts, sessions, and usage all live in Postgres.

---

## 1. Prerequisites

A Linux server (a small VM is fine) with **Docker** + the **Docker Compose
plugin**. A reachable address: an internal IP/hostname for office use, or a
public domain for internet use ([HTTPS](#6-https-for-production)). Postgres,
Python, and Node all live inside the containers.

## 2. Configure

```bash
git clone <your-repo-url> agentpy
cd agentpy/deploy
cp .env.example .env
```

Set at minimum:

| Variable | What to put |
|----------|-------------|
| `POSTGRES_PASSWORD` | A strong database password |
| `HARNESS_JWT_SECRET` | A long random string — `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

Leave the model settings as-is to start (offline scripted agent, no key), or set
a real model — [§5](#5-using-a-real-model). **`.env` holds secrets and is
git-ignored — never commit it.**

## 3. Launch

```bash
docker compose up -d --build
```

First run builds the images; Postgres creates its tables automatically. Verify:

```bash
docker compose ps
curl http://localhost/health
```

Live at **`http://<your-server>/`** (or the `HTTP_PORT` you set).

---

## 4. First login & creating real accounts

Two demo accounts are seeded so you can get in immediately:

| Username | Password | Role |
|----------|----------|------|
| `alice`  | `alice123` | admin |
| `bob`    | `bob123`   | user |

**Secure them before real use:**

1. Open the app and log in as **alice**.
2. **Admin dashboard → Add user**: create your own **admin** account and one
   account per teammate (`user` role for most people).
3. Log out; log back in as **your new admin**.
4. **Delete `alice` and `bob`** in the dashboard.

Onboarding a teammate from then on = one row in the **Add user** form.

## 5. How people use it on their laptops

Nothing to install. Each person:

1. Opens a browser to `http://<server>/` (network) or `https://<domain>/`
   (internet).
2. Logs in with the account you made them.
3. Gets their **own** isolated sessions, workspace, and history — a second user
   sees none of the first's work.

> Power users can also use the terminal CLI (`python main.py`) from a clone of
> the repo — but for everyone else, the browser is the whole story.

## 6. HTTPS for production

Over the internet, use HTTPS — logins and tokens ride every request. Easiest is
[Caddy](https://caddyserver.com/) in front (automatic certificates). Set
`HTTP_PORT=8080` in `.env`, then a one-line `Caddyfile`:

```
your-domain.com {
    reverse_proxy localhost:8080
}
```

Point DNS at the server, `caddy run`, and you're on `https://your-domain.com`.
Any TLS terminator (nginx, a cloud load balancer, Cloudflare) works — forward to
`HTTP_PORT`.

## 7. Using a real model

Default is the offline **scripted** provider (no key). For a real model, set in
`.env` and re-run `docker compose up -d`:

```bash
HARNESS_MODEL=openai/your-model
HARNESS_BASE_URL=https://llm.yourcompany.com
HARNESS_API_KEY=your-key
# optional fallback chain, tried left to right on failure:
HARNESS_FALLBACK_MODEL=gemini/gemini-2.0-flash,groq/llama-3.3-70b-versatile
```

Anthropic and any OpenAI-compatible endpoint work with no code change.
`demo/scripted` stays in the dropdown as a safety option.

## 8. Connecting MCP tool servers (GitHub, files, …)

Admins can give **every** session extra tools by connecting [MCP](https://modelcontextprotocol.io)
servers from **Admin dashboard → MCP tool servers**. Their tools show up
namespaced as `mcp__<server>__<tool>` and persist (they reconnect on restart).
Two transports:

- **URL (http/sse)** — a hosted MCP endpoint. Nothing to install; paste the URL.
- **stdio** — a command the backend launches. The backend image ships Node, so
  `npx`-based servers work out of the box. Examples (enter in the form):

  | Server | transport | command | args |
  |--------|-----------|---------|------|
  | GitHub | stdio | `npx` | `-y @modelcontextprotocol/server-github` |
  | Files  | stdio | `npx` | `-y @modelcontextprotocol/server-filesystem /data/workspaces` |

  A GitHub server needs a token — add it in the form's **env** as
  `GITHUB_PERSONAL_ACCESS_TOKEN`. The filesystem server can only reach paths
  **inside the backend container** (e.g. the `workspaces` volume at
  `/data/workspaces`), not a user's laptop.

After **Add & connect**, a green dot and the tool list mean it's live; a red dot
shows the connect error so you can fix and retry. stdio servers run a command on
the backend host, so this is admin-only.

## 9. Letting users connect their GitHub

Each user can authorize their own GitHub from the app (**🐙 Connect GitHub** in
the top bar) — no token pasting. Set it up once:

1. Create a **GitHub OAuth App** at <https://github.com/settings/developers> →
   *New OAuth App*.
2. **Authorization callback URL**: `https://<your-domain>/oauth/github/callback`
   (must match exactly).
3. Copy the **Client ID** and generate a **Client secret**, then set in `.env`:

   ```bash
   HARNESS_GITHUB_CLIENT_ID=Iv1.xxxx
   HARNESS_GITHUB_CLIENT_SECRET=xxxx
   HARNESS_PUBLIC_URL=https://<your-domain>   # so the callback is built right
   ```
4. `docker compose up -d`. Users click **Connect GitHub**, authorize on GitHub,
   and the agent can then act on their GitHub (via the `github_request` tool) as
   *them* — per user, isolated. Without these vars the button simply doesn't show.

## 10. Backups

Durable data is in two volumes: `pgdata` (database) and `workspaces` (files).

```bash
docker compose exec db pg_dump -U harness harness > backup-$(date +%F).sql
```

Restore with `psql`; schedule with cron.

## 11. Updating

```bash
cd agentpy && git pull && cd deploy && docker compose up -d --build
```

Database and workspaces are preserved (they're volumes); schema changes apply on
boot.

## 12. Hardening checklist

- [ ] Strong `POSTGRES_PASSWORD`, long random `HARNESS_JWT_SECRET`.
- [ ] Deleted the seeded `alice`/`bob` accounts (§4).
- [ ] HTTPS in front (§6) — never real logins over plain HTTP.
- [ ] `HARNESS_CONFINE_WORKSPACE=true` (default here) — agent stays off the host.
- [ ] For untrusted commands, consider `HARNESS_SANDBOX=docker` (see ../DEPLOY.md).
- [ ] Regular DB backups (§8).
- [ ] Firewall exposes only 80/443 — not the database port.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `curl /health` fails | `docker compose logs backend` — usually a bad `HARNESS_DB_URL` or the db still starting. |
| Login returns 401 | Seed accounts are `alice`/`alice123`, `bob`/`bob123`. |
| Everyone logged out after redeploy | `HARNESS_JWT_SECRET` changed/unset — set a fixed value in `.env`. |
| Replies don't stream | A front proxy is buffering; pass through without buffering (the bundled nginx already does). |
| "connection lost" mid-reply | Backend restarted; clean, recoverable — resend the message. |

---

**Verified:** the backend runs against real PostgreSQL — tables auto-create, and
logins, sessions, turns, and the admin usage dashboard all persist to and read
from Postgres.
