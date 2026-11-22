# mhsquare

Self-hosted tool that turns a YouTube script plus recorded narration into a finished video with
AI-generated visuals. Technical scenes are rendered from LLM-written Manim code. Exports MP4, FCPXML
and SRT.

Full spec: [mhsquare-build-spec.md](mhsquare-build-spec.md) · Architecture: [docs/architecture.md](docs/architecture.md) · Decisions: [docs/decisions.md](docs/decisions.md)

## Quick start

```sh
sh scripts/setup.sh            # Windows: powershell scripts/setup.ps1
#  → creates .env (random secrets), litellm/config.yaml, config/routing.yaml, installs git hooks
#  → prints your login email + password

# add provider keys to .env (ANTHROPIC_API_KEY, GEMINI_API_KEY), then:
docker compose up -d --build
```

Open http://localhost:8080 and log in. Then create the LiteLLM virtual key with a daily budget
([litellm/README.md](litellm/README.md)) and put it in `.env` as `MHSQUARE_LITELLM_API_KEY`.

Health: `curl localhost:8000/api/health/ready`

## Development

| | |
|---|---|
| Backend tests | `docker compose run --rm --no-deps api pytest` |
| Backend lint | `docker compose run --rm --no-deps api ruff check .` |
| UI dev server | `cd ui && npm install && npm run dev` (proxies `/api` to `localhost:8000`) |
| UI tests / lint | `cd ui && npm test && npm run lint` |

No test calls a paid provider. Real-provider smoke tests need `MHSQUARE_LIVE_TESTS=1`.

## Secrets

`.env`, `litellm/config.yaml` and `config/routing.yaml` are gitignored. Only `*.example` files are
committed. A gitleaks pre-commit hook is mandatory. `scripts/install-hooks.sh` installs it and uses
the gitleaks binary or Docker.

## Status

- [x] Phase 1 — Skeleton (compose stack, auth, dark app shell, health, hooks)
- [ ] Phase 2 — Ingest
- [ ] Phase 3 — Planning
- [ ] Phase 4 — Code animations
- [ ] Phase 5 — Images, keyframes, AI clips
- [ ] Phase 6 — Review, state machine, budget
- [ ] Phase 7 — Assembly and export
- [ ] Phase 8 — Mobile and polish
