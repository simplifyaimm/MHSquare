# Decisions

Choices the spec left open, the option picked, and why. Newest at the bottom of each phase.

## Phase 1 — Skeleton

| # | Decision | Why |
|---|---|---|
| 1 | **Server-side sessions** (`auth_sessions` table). The cookie holds a random token; only its sha256 is stored. | Logout really revokes the session; logging out on the phone doesn't kill the desktop session. |
| 2 | **CSRF = per-session token.** It is sent in a readable `mh_csrf` cookie and checked against the `X-CSRF-Token` header on every non-GET request. Login is exempt. | Combined with `SameSite=strict`, this covers CSRF without extra libraries. Login CSRF is mitigated by SameSite. |
| 3 | **Login rate limit is in-process**: 5 failures per IP per 5 min, configurable. | One user, one API process. Move it to Redis if the API is ever scaled out. |
| 4 | **Single user is bootstrapped from env** (`MHSQUARE_ADMIN_EMAIL/PASSWORD`) on first API start, only when no user exists. Placeholder passwords are refused. | Simplest single-user setup. `scripts/setup.sh` generates a random password. |
| 5 | **UI served by nginx on the same origin**, proxying `/api` to the API. | Same-origin cookies work with `SameSite=strict`, there are no CORS preflights, and SSE works with `proxy_buffering off`. CORS is still restricted to `MHSQUARE_UI_ORIGIN` for the Vite dev server. |
| 6 | **The Manim renderer is a long-running service on an `internal: true` Docker network**, not a per-render `docker run`. The container is read-only, uses a tmpfs `/tmp`, runs as non-root, drops all caps, sets `no-new-privileges`, and has CPU/mem/pids limits. Phase 4 adds a subprocess timeout plus rlimits per render. | The alternative is mounting the Docker socket into the worker, which is root-equivalent on the host. That is a bigger risk than the untrusted code it would isolate. An internal network has no internet egress. |
| 7 | **Migrations run at API start** (`alembic upgrade head`). The worker waits for API health. | No separate migrate step to forget. |
| 8 | **The gitleaks hook has two paths.** `.pre-commit-config.yaml` is for the pre-commit framework. `.githooks/pre-commit` has zero dependencies and runs the gitleaks binary or the `zricethezav/gitleaks` Docker image. `scripts/setup.sh` installs the latter. | The dev host has no Python, so the pre-commit framework can't be assumed. |
| 9 | **Fonts are self-hosted** via `@fontsource-variable/*` (Inter, JetBrains Mono). | The PWA works offline and makes no third-party font requests. |
| 10 | **shadcn/ui components are hand-added** under `ui/src/components/ui` (same pattern: cva + tailwind-merge). Tailwind v4 with tokens in `@theme`. | shadcn is copy-in code anyway. This avoids the CLI's interactive init. |
| 11 | **LiteLLM uses the `main-stable` image tag** and its own `litellm` database in the shared Postgres. | Virtual keys and budgets need a DB. Pin a specific tag once the stack is validated. |
| 12 | **IDs are UUIDs** (SQLAlchemy `Uuid`). Tests run against SQLite. | Portable. No sequential IDs leak in URLs. |
