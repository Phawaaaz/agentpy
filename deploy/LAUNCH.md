# Launch checklist — Floowpay AI

Copy-paste steps to take `main` from a clone to a running, secured deployment.
Full detail is in [`deploy/README.md`](./README.md); this is the fast path.

Pre-flight status (verified on `main`): backend imports · 28/28 unit tests ·
frontend builds · 22/22 end-to-end smoke checks · `.env` git-ignored · no
committed secrets. The one thing to confirm live is a **real model** (step 4).

---

## 1. Server + code

A Linux box with **Docker** + the **compose plugin**. Then:

```bash
git clone <your-repo-url> agentpy
cd agentpy/deploy
cp .env.example .env
```

## 2. Fill in `.env` (minimum)

```bash
# --- required ---
POSTGRES_PASSWORD=<a strong random password>
HARNESS_JWT_SECRET=<paste output of: python -c "import secrets;print(secrets.token_urlsafe(48))">

# --- your admin login (recommended: skips the demo alice/bob accounts) ---
HARNESS_ADMIN_USERNAME=<your admin username>
HARNESS_ADMIN_PASSWORD=<a strong password>

# --- port (80 = http://server/) ---
HTTP_PORT=80
```

> `.env` holds secrets and is git-ignored — never commit it.

## 3. Launch

```bash
docker compose up -d --build
docker compose ps            # all healthy?
curl http://localhost/health # {"status":"ok",...}
```

App is live at `http://<your-server>/`.

## 4. Point at a real model + test it (the one live check)

Add to `.env`, then `docker compose up -d` again:

```bash
HARNESS_MODEL=openai/<your-model>        # or anthropic/claude-...
HARNESS_BASE_URL=https://llm.yourco.com  # your OpenAI-compatible endpoint
HARNESS_API_KEY=<key>
# optional fallback chain, tried left-to-right:
HARNESS_FALLBACK_MODEL=gemini/gemini-2.0-flash,groq/llama-3.3-70b-versatile
```

Then log in and confirm **live**:
- a reply **streams** token-by-token;
- ask it to *create a file listing the planets and show it* → tool cards run and
  a **download chip** appears under the reply;
- ask it to *read /etc/passwd* → the **sandbox blocks** it (red card).

`demo/scripted` stays in the model picker as an always-works fallback.

## 5. Accounts

If you set `HARNESS_ADMIN_USERNAME`/`PASSWORD` in step 2, that's your only
account and there are **no demo logins** — just log in and add your team from
**⚙ Admin dashboard → Add user**.

If you left them unset, the demo `alice/alice123` (admin) and `bob/bob123`
accounts are seeded — log in as alice, create your real admin + teammates, then
**delete `alice` and `bob`**.

## 6. HTTPS (for anything past localhost)

Logins ride every request — terminate TLS in front. Easiest is Caddy: set
`HTTP_PORT=8080` in `.env`, then

```
your-domain.com {
    reverse_proxy localhost:8080
}
```

Point DNS at the box, `caddy run`. (Any TLS proxy works — see README §6.)

## 7. Optional integrations

- **GitHub "Connect GitHub"** — register a GitHub OAuth App and set
  `HARNESS_GITHUB_CLIENT_ID` / `HARNESS_GITHUB_CLIENT_SECRET` /
  `HARNESS_PUBLIC_URL` (README §9). Without them the button stays hidden.
- **MCP tool servers** — Admin dashboard → MCP tool servers (README §8).
- **Agent skills** — users install SKILL.md zips from the 🧩 panel.

## 8. Tell your team

They install nothing: open `https://<domain>/`, log in with the account you
made them, click **? Help** for the tour. Each user's sessions, files, and
history are private to them.

---

## Hardening checklist

- [ ] Strong `POSTGRES_PASSWORD`; long random `HARNESS_JWT_SECRET` (a fixed
      value — otherwise everyone is logged out on each restart).
- [ ] Deleted seeded `alice`/`bob`.
- [ ] HTTPS in front — never real logins over plain HTTP.
- [ ] `HARNESS_CONFINE_WORKSPACE=true` (default) — agent stays off the host.
- [ ] For untrusted commands, consider `HARNESS_SANDBOX=docker` (see ../DEPLOY.md).
- [ ] Firewall exposes only 80/443 — not Postgres.
- [ ] Regular DB backups: `docker compose exec db pg_dump -U harness harness > backup-$(date +%F).sql`

## Updating later

```bash
cd agentpy && git pull && cd deploy && docker compose up -d --build
```

Data (Postgres + workspaces) lives in volumes and is preserved; schema changes
apply on boot.
