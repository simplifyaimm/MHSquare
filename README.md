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

docker compose up -d --build
```

Open http://localhost:8080, log in, and go to **Settings**. Paste your provider API keys, choose a
model for each role, and set a daily budget. They are stored in `local/llmconfig.env`, which is
gitignored and never committed. See [litellm/README.md](litellm/README.md).

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

`.env`, `local/llmconfig.env` (provider keys, written by the UI), `litellm/config.yaml` and
`config/routing.yaml` are gitignored. Only `*.example` files are
committed. A gitleaks pre-commit hook is mandatory. `scripts/install-hooks.sh` installs it and uses
the gitleaks binary or Docker.

## Status

- [x] Phase 1 — Skeleton (compose stack, auth, dark app shell, health, hooks)
- [x] Settings → AI providers (keys/models/budget from the UI → `local/llmconfig.env`)
- [x] Phase 2 — Ingest (chunked uploads, −14 LUFS cleanup, WhisperX word timestamps, filler/pause cuts + review, face-cam cuts, final transcript)
- [x] Phase 3 — Planning (single-pass planner, word-synced briefs, shot list edit/split/merge, estimate)
- [x] Phase 4 — Code animations (as HTML/GSAP motion scenes in a sandboxed Chromium; tech + story themes; character rigs)
- [~] Phase 5 — Images, keyframes, AI clips (image + overlay path built; needs an image/video provider with credit)
- [~] Phase 6 — Review, state machine, budget (takes, accept, regenerate-with-note, error→state mapping; SSE/runs/auto-resume pending)
- [x] Draft narration (TTS) + 720p preview render
- [ ] Phase 7 — Assembly and export
- [ ] Phase 8 — Mobile and polish
