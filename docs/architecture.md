# Architecture

```
 browser / PWA ──► ui (nginx :8080) ──/api──► api (FastAPI) ──► postgres
                                               │   ▲              redis ◄── worker (Celery)
                                               │   └── SSE ──────────────────┘
                                               ├──► litellm proxy ──► providers (keys live only here)
                                               └──► manim-renderer (internal network, no egress)
```

| Service | Role | Networks |
|---|---|---|
| `ui` | Static PWA build + reverse proxy for `/api` | core |
| `api` | REST + SSE, auth, migrations on start | core, sandbox |
| `worker` | Celery: ingest, planning, generation, polling, assembly | core, sandbox |
| `postgres` | App DB `mhsquare` + LiteLLM DB `litellm` | core |
| `redis` | Celery broker/results, pub/sub for SSE | core |
| `litellm` | Only gateway to LLM/image/video providers. Budgets via the virtual key. | core |
| `manim-renderer` | Renders untrusted LLM-written Manim code | sandbox (`internal: true`) |

## Rules enforced by structure

- `backend/app/llm/` is the only package that calls LiteLLM. Business logic uses `visual_type` and
  routing aliases from `config/routing.yaml`. It never uses provider names.
- The UI talks only to `/api`. Keys never leave the `litellm` container's env.
- Every shot and take state is a DB row, so workers can crash and restart safely (phases 5–6).

## Auth

Single user. Argon2id password hash. Server-side session (`auth_sessions`) referenced by an
httpOnly + Secure + SameSite=strict cookie `mh_session`. A per-session CSRF token is echoed via the
`X-CSRF-Token` header. Failed logins are rate-limited per IP.

## Remote access

Never expose ports directly. All published ports bind to `127.0.0.1`. To reach mhsquare from a phone:

- **Tailscale:** `tailscale serve --bg 8080` → `https://<machine>.<tailnet>.ts.net`
- **Cloudflare Tunnel:** `cloudflared tunnel --url http://localhost:8080` (put Cloudflare Access in front)

Set `MHSQUARE_UI_ORIGIN` to the public HTTPS origin.
