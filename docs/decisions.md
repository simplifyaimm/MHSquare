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

## LLM settings from the UI (requested after phase 1)

| # | Decision | Why |
|---|---|---|
| 13 | **Provider keys, model IDs and the mhsquare virtual key live in `local/llmconfig.env`** (gitignored). They are edited from Settings → AI providers. Keys are write-only through the API: reads return `configured` plus a `…last4` hint. | Requested: configure everything from the UI and never commit it. |
| 14 | **The API writes the file but never uses provider keys.** All model calls still go through LiteLLM with the virtual key. This is a narrow deviation from "backend only knows the LiteLLM URL + virtual key": the API also holds the LiteLLM master key, used only for `/key/generate` and `/key/update` (the budget screen). | Removes the manual curl step for creating the budgeted key. |
| 15 | **LiteLLM is wrapped by `litellm/entrypoint.sh`.** It loads `llm-defaults.env` and then `llmconfig.env` (parsed, never `source`d), and restarts the proxy when either changes, polled every 3 s. Lines starting `MHSQUARE_` are ignored for change detection. | Applying new keys doesn't need the Docker socket in the API, and saving the budget key doesn't bounce the proxy. |
| 16 | **`local/` is mounted as a directory**, not as a single file. | Single-file bind mounts don't follow atomic rename, and Docker creates a directory when the file is missing. |
| 17 | **Model aliases are provider-neutral**: `planner-llm`, `code-llm`, `code-llm-backup`, `image-gen`, `video-gen`, `video-gen-fast`. | The provider behind an alias is now a UI choice. |
| 18 | **Strict validation of values**: keys must be 8–512 printable ASCII characters with no whitespace. Model IDs must match `provider/model` with a safe character set. | Prevents newline/env injection into the file the proxy loads. |

## Phase 2 — Ingest

| # | Decision | Why |
|---|---|---|
| 19 | **WhisperX runs in its own `transcriber` container** (backend image `ml` target: CPU torch 2.8 + whisperx 3.8.6, about 5 GB). It consumes only the Celery `transcribe` queue with concurrency 1. Models are cached in the `models` volume. The api/worker images stay slim. | The spec allows either. Keeping torch out of the API image keeps it small, and transcription never starves other tasks. |
| 20 | **Default Whisper model is `small`** on CPU int8 (`MHSQUARE_WHISPER_MODEL`). Word timing comes from wav2vec2 forced alignment, so it's accurate even with small ASR models. | A good speed/accuracy trade-off on CPU. Use `medium` or `large-v3` if a GPU is added. |
| 21 | **A filler-laden `initial_prompt`** ("Umm, let me think like, hmm…") is passed to Whisper. | Whisper normally drops disfluencies. With the prompt it kept "Um," and "uh," in the real test. |
| 22 | **"like" counts as a filler only when set off by punctuation** ("like,") or when it's the first word. | "I like Kafka" must not be cut. |
| 23 | **Cut shape**: a gap containing fillers is cut from the filler through the dead air, keeping up to `keep_pause_ms` (150) of natural pause next to real speech. Pauses longer than `silence_ms` (600) shrink to 2 × 150 ms. Edges are trimmed fully. Cuts shorter than 80 ms are dropped. | Sounds natural. It never clips real words. |
| 24 | **Cut boundaries snap to the project frame grid** (e.g. 1/30 s) before they are applied to both the audio and the face-cam. | Per-segment frame rounding would otherwise accumulate audio/video drift over hundreds of cuts. |
| 25 | **Audio cuts use trim + concat with 4 ms fades** per segment. | Avoids clicks at cut points. |
| 26 | **The face-cam is cut once into a CFR H.264 CRF 16 intermediate** at the project fps. There's an optional `facecam_offset_ms` for when the camera and the mic didn't start together. | One high-quality encode. The final render happens once in phase 7, per principle 8. |
| 27 | **Loudness cleanup = high-pass 80 Hz → gain → 4×-oversampled peak limiter at −1.5 dBTP**, iterated with a secant step until within ±0.5 LU of −14 LUFS (max 4 passes, gain capped at ±30 dB). It is not ffmpeg `loudnorm`. | Single- and two-pass loudnorm both undershot speech: −16.8 and −16.3 LUFS on the test clip. Loudnorm can't add the needed gain under the peak ceiling, so it falls back to dynamic mode. The new chain measured −14.0 LUFS / −1.5 dBTP. The cleaner stays pluggable (`media._CLEANERS`). |
| 28 | **Uploads are resumable chunked PUTs** (`POST /projects/{id}/uploads` → `PUT /uploads/{id}?offset=` in 8 MB chunks → `POST …/complete`), plus single-request multipart `POST /projects/{id}/assets` for small files. Checks: an extension allow-list, a declared-size limit, magic-byte sniffing, ffprobe stream checks, and a stored extension derived from the sniffed type. | Meets the spec's "chunked upload for large files". A resumed upload asks the server for its offset. |
| 29 | **Signed media URLs**: HMAC of asset id + expiry (6 h TTL), with single-range `Range` support. They don't need the session cookie. | `<audio>`/`<video>` tags can scrub, and links expire. |
| 30 | **Ingest status is polled by the UI** (every 2 s while busy) in phase 2. SSE arrives in phase 6. | Simplest for now. The state lives in the DB either way. |
| 31 | **The UI must run vitest from an uppercase `D:\` cwd on Windows.** A lowercase drive letter loads two vitest instances ("failed to find the current suite"). | Known Windows path-casing issue. CI and Docker are unaffected. |
