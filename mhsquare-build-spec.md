# mhsquare — Build Specification for Claude Code

> Hand this file to Claude Code and ask it to build the codebase phase by phase.
> Claude Code: read the whole file before writing any code. Work through the phases in order.
> At the end of each phase, run the tests, make sure `docker compose up` works, and commit.
> When this spec doesn't settle a choice, pick the simplest option and record it in `docs/decisions.md`.

---

## 1. What mhsquare is

mhsquare is a self-hosted tool that turns a **YouTube script plus my own recorded narration** into a **finished video whose visuals are all AI-generated**.

The flow:

1. I upload the script and my recorded audio (optionally with a face-cam video).
2. mhsquare cleans the audio, transcribes it with word-level timestamps, and plans a **shot list** for the whole video.
3. Each shot is routed by **visual type** to a renderer or model provider:
   - code-rendered technical animation
   - AI video clip
   - still image
   - text card
   - my face-cam (A-roll)
4. I review every shot in the UI and either accept it or regenerate it with a feedback note.
5. When the daily AI budget runs out, generation pauses. I resume it the next day from the UI.
6. mhsquare assembles the final video itself and exports:
   - MP4
   - FCPXML (so I can polish it in DaVinci Resolve)
   - SRT captions

I control everything from the UI, which works on desktop and mobile (installable PWA).

**Main differentiator:** technical scenes (architecture diagrams, data flows, Kafka/Kubernetes/AWS concepts) are **rendered from LLM-written code**, so text stays sharp and diagrams stay accurate. Generative video is used only for character, mood and metaphor shots.

---

## 2. Non-negotiable principles

1. **Route by visual type, not by provider.** The LLM picks a `visual_type`. A config file (`routing.yaml`) maps each visual type to a model, with fallbacks. Provider and model names never appear in business logic.
2. **All LLM, image and video calls go through a LiteLLM proxy.** This gives usage tracking, cost tracking and budgets. The backend only knows the LiteLLM URL and a LiteLLM virtual key.
3. **No secrets in git.**
   - The real LiteLLM config, `.env` files and provider keys are gitignored.
   - Only `*.example` files are committed.
   - A `gitleaks` pre-commit hook is mandatory.
4. **The UI never holds keys or calls providers.** UI → backend API only.
5. **LLM-generated code is untrusted.** It runs only inside sandboxed renderer containers: no network, CPU/memory limits, timeouts.
6. **Never pay twice.**
   - Persist provider operation IDs before polling.
   - Cache outputs by a hash of their inputs.
   - Download outputs to our storage immediately, because provider URLs expire.
7. **Every shot's state is persisted.** Any process can crash or restart and the project resumes exactly where it was.
8. **Render the final video once, at high quality.** Conform all clips to one resolution and frame rate first. No repeated lossy re-encodes.

---

## 3. Tech stack (decided)

| Layer | Choice |
|---|---|
| UI | React + TypeScript + Vite, Tailwind CSS, shadcn/ui, TanStack Query, React Router. PWA via `vite-plugin-pwa`. |
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic |
| Worker | Celery with a Redis broker (a separate process from the API) |
| DB | PostgreSQL 16 (Docker) |
| Storage | Local volume behind a storage interface (`LocalStorage` now, `S3Storage` later) |
| LLM gateway | LiteLLM proxy (separate container) |
| Transcription | WhisperX (word-level timestamps), run in the worker or its own container |
| Code animations | Manim Community Edition, in a sandboxed renderer container |
| Assembly | ffmpeg (subprocess) for rendering; OpenTimelineIO for FCPXML export |
| Live updates | Server-Sent Events (SSE) from API to UI |
| Push notifications | Web Push (VAPID) through the PWA service worker |
| Auth | Single-user login (argon2 password hash), httpOnly session cookie, CSRF protection |
| Packaging | `docker-compose.yml` for the whole stack |

---

## 4. Repository layout

```
mhsquare/
├── ui/                         # React PWA (separate module)
├── backend/
│   ├── app/
│   │   ├── api/                # FastAPI routers
│   │   ├── core/               # config, auth, security, logging
│   │   ├── models/             # SQLAlchemy models
│   │   ├── schemas/            # Pydantic schemas
│   │   ├── services/
│   │   │   ├── ingest/         # audio cleanup, filler removal, transcription
│   │   │   ├── planner/        # shot list planning
│   │   │   ├── routing/        # visual_type -> model resolution
│   │   │   ├── generation/     # keyframe/image/video/code generation adapters
│   │   │   ├── review/         # state machine transitions
│   │   │   ├── budget/         # LiteLLM spend + pause/resume logic
│   │   │   ├── assembly/       # conform, timeline, ffmpeg render, exports
│   │   │   └── storage/        # storage interface
│   │   ├── llm/                # the ONLY place that talks to LiteLLM
│   │   └── workers/            # Celery tasks
│   ├── alembic/
│   └── tests/
├── renderers/
│   └── manim/                  # Dockerfile + runner (no network)
├── litellm/
│   ├── config.example.yaml     # committed
│   ├── config.yaml             # GITIGNORED
│   └── README.md               # how to create config.yaml + virtual key
├── config/
│   ├── routing.example.yaml    # committed
│   └── routing.yaml            # gitignored (copied from example)
├── docs/
│   ├── architecture.md
│   └── decisions.md
├── docker-compose.yml
├── .env.example                # committed
├── .env                        # GITIGNORED
├── .gitignore
├── .pre-commit-config.yaml     # gitleaks + ruff + eslint
└── README.md
```

---

## 5. Pipeline in detail

### 5.1 Ingest (run in this order — the order matters)

1. **Upload.** Inputs:
   - script (`.md`/`.txt`)
   - narration audio (`.wav`/`.m4a`/`.mp3`)
   - optional face-cam video
   - 1–3 character reference images
   - optional style notes
2. **Audio cleanup.**
   - ffmpeg high-pass filter plus `loudnorm` to −14 LUFS for YouTube.
   - Keep the cleanup step pluggable, so an external enhancer can be added later.
3. **First transcription pass** with WhisperX to get word timestamps.
4. **Filler and silence removal.**
   - Detect fillers ("um", "uh", "like", configurable) and silences longer than a configurable threshold (default 600 ms).
   - Show the proposed cuts in the UI.
   - When I approve, apply the same cuts to the audio and the face-cam video.
5. **Final transcription** of the cleaned audio. These timestamps are the **source of truth**. Cuts change timing, so shots must never be planned against the uncut audio.

### 5.2 Shot planning (a single LLM pass)

- Make **one** LLM call over the full cleaned transcript plus the original script (for intent).
- The output is a JSON shot list validated against a Pydantic schema. If validation fails, retry once with the validation errors included in the prompt.
- Each shot contains:

```json
{
  "index": 7,
  "start_ms": 41200,
  "end_ms": 48900,
  "transcript_text": "...",
  "visual_type": "code_animation | ai_clip | still_image | text_card | a_roll",
  "prompt": "...",
  "depends_on_previous": false,
  "uses_character": true,
  "notes": "why this visual"
}
```

- The plan also includes a **global style guide**: palette, mood, camera language, character description, typography for text cards. It is injected into every generation prompt.
- Rules for the planner prompt:
  - At least 30% of runtime should be `a_roll` if face-cam exists. Otherwise prefer `code_animation` for technical explanations.
  - `ai_clip` shots must be ≤ 8 s. Split longer segments into multiple shots.
  - Prefer `code_animation` whenever on-screen text, labels, diagrams or code must be readable.
  - Don't put the same visual on consecutive shots.
- I can edit the shot list in the UI (change type, prompt, boundaries, merge/split) before generation starts.

### 5.3 Routing (`config/routing.yaml`)

```yaml
visual_types:
  code_animation:
    generator: manim_code          # LLM writes Manim code -> sandbox renders it
    llm_model: claude-code-gen     # LiteLLM model alias
    fallbacks: [claude-code-gen-backup]
  ai_clip:
    generator: video
    model: veo-video               # LiteLLM alias -> e.g. gemini/veo-3.1-*
    keyframe_model: image-gen
    durations_allowed: [4, 6, 8]
    fallbacks: [veo-video-fast]
  still_image:
    generator: image
    model: image-gen
    motion: ken_burns              # slow pan/zoom applied at assembly
  text_card:
    generator: template            # rendered locally, no AI cost
  a_roll:
    generator: passthrough         # my face-cam footage
planner:
  model: claude-planner
```

- Model aliases are defined in the LiteLLM config, not here. Claude Code: check current model IDs in the LiteLLM docs at build time. LiteLLM's `/videos` API supports Gemini/Vertex Veo. Note that the OpenAI Sora API has been shut down, so don't use it.
- Routing resolution order:
  1. per-shot override from the UI
  2. `routing.yaml`
  3. fallbacks, used on provider errors (not on content-policy errors)

### 5.4 Generation

#### Code animation (`manim_code`)

1. The LLM receives:
   - the shot prompt and style guide
   - the target duration (`end_ms - start_ms`)
   - the resolution and fps
   - a Manim system prompt with house style, allowed imports and a template scene class
2. Static checks on the returned code:
   - AST parse
   - only whitelisted imports (`manim`, `numpy`, `math`)
   - no file, network, `os` or `subprocess` access
3. Render in the `renderers/manim` container with:
   - `--network none`
   - read-only filesystem except `/out`
   - CPU and memory limits
   - 180 s timeout
4. If rendering fails, send the error back to the LLM once for a fix, then mark the shot `failed` with the logs.
5. Time-fit the result to the exact shot duration: pad with the last frame, or adjust the `wait()` calls.

#### AI clip (`video`) — two gates

1. **Keyframe gate.** Generate one still image (style guide + character references + prompt). I approve or regenerate it in the UI. This is cheap.
2. **Video gate.** Generate the clip with the approved keyframe as the first frame.
   - If `uses_character`, pass the character reference images (Veo supports up to 3).
   - If `depends_on_previous`, use the previous accepted take's **last frame** as this shot's first frame. Such shots are generated strictly in sequence.
3. Async flow:
   - submit → **persist `provider_operation_id` immediately** → poll with backoff → download to storage → record cost.

#### Still image (`image`)

- Generate the image, then review it. Motion (Ken Burns) is applied at assembly.

#### Text card (`template`)

- Render locally from a template (title, bullet, code snippet) with an ffmpeg or Pillow renderer. No AI cost.

#### Cache

- Key: `sha256(visual_type, model, prompt, style_guide_version, refs_hash, seed, duration, resolution)`.
- If the key matches an existing take, reuse that file instead of making a new paid call.

---

## 6. Review flow and the shot state machine

```
planned ──► keyframe_generating ──► keyframe_review ──► queued
   │                                     │ reject(note)        │
   │                                     └──► keyframe_generating
   │
   └─(non-ai_clip types skip keyframe)──────────────────────► queued

queued ──► generating ──► review ──► accepted
               │            │ reject(note) ──► queued (new take)
               │            └──────────────────────────────
               ├──► paused_budget   (resume ──► queued)
               ├──► needs_edit      (content-policy block; I edit the prompt ──► queued)
               └──► failed          (retry ──► queued)
```

- Each generation attempt is a **Take** linked to its Shot. All takes are kept, and I can pick any take as the accepted one.
- **Reject with note:** my note is appended to the prompt as "Revision feedback: …" for the next take.
- **Run modes** (selectable in the UI):
  - **Step mode (default):** generate one shot, wait for my review, then continue. Dependent shots always follow this.
  - **Batch mode:** independent shots (code animations, images, text cards) generate in parallel, up to a concurrency limit, and I review them together.
- **Seed:** stored per take. "Regenerate with same seed" and "lock seed" options are available.

---

## 7. Budget, pause and resume

- The LiteLLM proxy issues a **virtual key for mhsquare** with `max_budget` and `budget_duration: 1d`. That makes the daily stop deterministic.
- Before each paid call, the budget service checks remaining spend through the LiteLLM API. If the estimated cost is more than what's left, the shot goes to `paused_budget` and the run pauses.
- Error classification (in `app/llm/`), mapped to states:

| Error | Action |
|---|---|
| budget exceeded / insufficient quota | `paused_budget`, pause run, push notification |
| 429 rate limit | exponential backoff with jitter, up to 5 tries, then `failed` |
| content policy / safety block | `needs_edit` (no fallback) |
| 5xx / timeout | retry 3×, then try the fallback model, then `failed` |

- **Resume:**
  - Manual button in the UI.
  - Optional auto-resume after the budget resets (checked by a Celery beat task), which sends a push notification: "Budget reset — resume mhsquare run?"
- **Restart safety:** on worker start, find takes in `generating` that have a `provider_operation_id` and resume polling them. Don't resubmit them.
- **Cost estimate:** before a run starts, show the estimated cost per shot and in total, using per-second and per-image prices from LiteLLM's cost map.

---

## 8. Assembly and export

1. **Conform.** Normalise every accepted take to the project format: default 1920×1080, 30 fps, yuv420p, constant frame rate. 3840×2160 is selectable.
2. **Build a timeline model**:
   - base audio track: cleaned narration
   - video track: accepted takes at `start_ms`, with A-roll where planned
   - Ken Burns on stills
   - optional crossfades (default 0 frames, cut only)
3. **Render once with ffmpeg**:
   - H.264 High profile, CRF 18, `-preset slow`, AAC 320 kbps
   - option for an intermediate ProRes master
4. **Captions.** Generate an SRT from the final transcript. Burned-in captions are optional, with a style from the style guide.
5. **Exports**:
   - `final.mp4`
   - `timeline.fcpxml` (via OpenTimelineIO, referencing the conformed media files)
   - `captions.srt`
   - `project_bundle.zip` (media + fcpxml) for DaVinci Resolve
6. A thumbnail sheet and a low-resolution proxy preview are generated for mobile review.

---

## 9. Data model (minimum)

- `User`: id, email, password_hash, web_push_subscriptions
- `Project`: id, name, status, resolution, fps, style_guide (JSON, versioned), created_at
- `Asset`: id, project_id, kind (script/audio/facecam/character_ref/output), storage_key, sha256, meta
- `Transcript`: id, project_id, version, words (JSON with timestamps), is_final
- `CutProposal`: id, project_id, ranges (JSON), approved
- `Shot`: id, project_id, index, start_ms, end_ms, transcript_text, visual_type, prompt, depends_on_previous, uses_character, state, accepted_take_id, override_model
- `Take`: id, shot_id, attempt, kind (keyframe/final), model, prompt_used, seed, feedback_note, provider_operation_id, cache_key, storage_key, cost_usd, status, error, logs
- `Run`: id, project_id, mode (step/batch), status (running/paused_budget/paused_manual/completed), started_at, paused_at
- `Export`: id, project_id, kind, storage_key, created_at

---

## 10. API (FastAPI, all under `/api`, auth required except login)

- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`
- `POST /projects/{id}/assets` (multipart, chunked upload for large files)
- `POST /projects/{id}/ingest` → cleanup + transcription job
- `GET /projects/{id}/cuts`, `POST /projects/{id}/cuts/approve`
- `POST /projects/{id}/plan`, `GET /projects/{id}/shots`, `PATCH /shots/{id}`, `POST /shots/{id}/split`, `POST /shots/merge`
- `GET /projects/{id}/estimate`
- `POST /projects/{id}/runs` (start, mode), `POST /runs/{id}/pause`, `POST /runs/{id}/resume`
- `POST /shots/{id}/takes/{take_id}/accept`, `POST /shots/{id}/regenerate` (body: note, keep_seed)
- `GET /projects/{id}/events` → SSE stream (shot state changes, progress, budget)
- `GET /usage` → spend from LiteLLM (today, this month, per model, per project)
- `POST /projects/{id}/assemble`, `GET /projects/{id}/exports`
- `GET /settings/routing`, `PUT /settings/routing` (validated against the schema)
- `POST /push/subscribe`
- Media is served through short-lived signed URLs, with HTTP range support so video can be scrubbed.

---

## 11. UI — mhsquare (dark theme)

### Brand and theme

- Name shown as **mhsquare** (lowercase). Logo: two overlapping squares containing "mh", as an inline SVG.
- **Dark theme only** for v1. Design tokens in Tailwind / CSS variables:

| Token | Value |
|---|---|
| `--bg` | `#0B0D12` |
| `--surface` | `#12151C` |
| `--surface-2` | `#1A1F29` |
| `--border` | `#262C38` |
| `--text` | `#E6E9EF` |
| `--text-muted` | `#8A93A6` |
| `--accent` | `#7C5CFF` (violet) |
| `--accent-2` | `#22D3EE` (cyan, for progress/live states) |
| `--success` | `#22C55E` |
| `--warning` | `#F59E0B` |
| `--danger` | `#EF4444` |

- Font: Inter for UI text, JetBrains Mono for code, prompts and timestamps.
- Shot states get consistent colour chips:
  - planned = muted
  - generating = cyan pulse
  - review = violet
  - accepted = green
  - paused = amber
  - needs_edit/failed = red
- Mobile-first responsive layout. Bottom tab bar on mobile, sidebar on desktop.

### Screens

1. **Login**
2. **Projects**: cards with status, progress (accepted/total shots), today's spend
3. **New project wizard**: upload script → upload audio/face-cam → character refs → style notes → start ingest
4. **Cuts review**: waveform with proposed filler/silence cuts. Toggle each cut, approve all.
5. **Shot list**:
   - timeline strip plus a list of shots
   - edit type, prompt and boundaries; split/merge
   - per-shot cost estimate
   - "Start run" with step/batch mode
6. **Shot review** (the most important screen, and it must work well on a phone):
   - large player for the current take, with transcript text and timestamps
   - **Accept** / **Regenerate** (note field, keep-seed toggle)
   - take history carousel, with a "use this take" option
   - previous/next shot; swipe on mobile
7. **Run control**: running/paused banner, budget meter (spent/limit, resets in hh:mm), Pause/Resume buttons, live event log
8. **Assemble and export**: preview the proxy render, render the final, download MP4/FCPXML/SRT/bundle
9. **Usage**: spend charts per day, model and project (from `/usage`)
10. **Settings**: routing editor (form view plus raw YAML with validation), budget display, push notifications on/off, default resolution/fps

### PWA

- Installable, with app icons generated from the logo and a dark splash screen.
- Push notifications for:
  - shot ready for review
  - run paused (budget)
  - budget reset
  - export ready
- Offline: show the cached project list and a clear "offline" state. No offline generation.

---

## 12. Security checklist

- `.gitignore` includes:
  - `.env`
  - `litellm/config.yaml`
  - `config/routing.yaml`
  - `storage/`
  - `*.pem`
  - `service_account*.json`
- `.pre-commit-config.yaml`: gitleaks, ruff, eslint, prettier.
- The LiteLLM config reads provider keys only via `os.environ/…`. `litellm/README.md` explains how to:
  - create `config.yaml` from the example
  - start the proxy
  - create the mhsquare virtual key with a daily budget
- Backend:
  - argon2 password hashing
  - httpOnly + Secure + SameSite=strict session cookie
  - CSRF token
  - rate-limited login
  - CORS restricted to the UI origin
- File uploads: MIME and size validation, stored under random keys, never executed.
- The Manim sandbox runs as a non-root user with `--network none`, resource limits, a timeout and an import whitelist.
- Remote access is documented for Tailscale or Cloudflare Tunnel. The API is never exposed without auth.

---

## 13. Build phases (Claude Code: follow in order)

**Phase 1 — Skeleton**
- Monorepo layout and docker-compose: ui, api, worker, redis, postgres, litellm, manim-renderer.
- Auth, dark-theme app shell and logo, health checks.
- Pre-commit hooks, example config files.
- *Done when:* `docker compose up` works, I can log in, and gitleaks blocks a fake key.

**Phase 2 — Ingest**
- Uploads, audio cleanup, WhisperX transcription, cut proposals, Cuts review screen, final transcript.
- *Done when:* a sample audio file produces an approved cleaned file plus a final transcript with word timestamps.

**Phase 3 — Planning**
- Planner prompt and schema, shot list API, Shot list screen with edit/split/merge, cost estimate.
- *Done when:* a sample script produces a valid shot list that I can edit.

**Phase 4 — Code animations**
- Manim generation, static checks, sandbox render, time-fit, retry-on-error.
- *Done when:* a technical shot renders to an MP4 of the exact duration.

**Phase 5 — Images, keyframes and AI clips**
- Image and Veo adapters through LiteLLM, keyframe gate, reference images, last-frame chaining, operation-ID persistence, cache.
- *Done when:* an `ai_clip` shot goes keyframe → approve → clip, and survives a worker restart mid-generation.

**Phase 6 — Review, state machine and budget**
- Full state machine, step/batch modes, takes, regenerate-with-note.
- SSE events, budget checks, error classification, pause/resume, auto-resume beat task, Run control screen.
- *Done when:* setting the virtual key budget very low makes the run pause cleanly, and resume continues from the same shot.

**Phase 7 — Assembly and export**
- Conform, timeline, ffmpeg render, captions, FCPXML via OpenTimelineIO, bundle, proxy preview.
- *Done when:* the FCPXML imports into DaVinci Resolve with clips at the correct timecodes, and the MP4 plays with audio in sync.

**Phase 8 — Mobile and polish**
- PWA install, Web Push, mobile layout for Shot review (swipe, big buttons), Usage screen.
- *Done when:* I can install mhsquare on my phone, get a "shot ready" push, and accept or regenerate from the phone.

---

## 14. Testing

- Backend: pytest.
  - Unit tests: state machine transitions, routing resolution, cache keys, error classification, budget logic, timeline building.
  - Integration tests with a **fake LiteLLM** (a mock server that returns canned image/video/code responses and can simulate 429, budget-exceeded, policy-block and 5xx errors).
- Renderer: test that a known-good Manim scene renders and a malicious one (network or `os` access) is rejected.
- UI: Vitest for components. Playwright for the critical flows: login, create project, review and accept a shot, pause/resume.
- No test may call a real paid provider. Real-provider smoke tests sit behind the `MHSQUARE_LIVE_TESTS=1` environment variable.

---

## 15. Out of scope for v1

- Multi-user / teams
- Light theme
- Native app-store builds (PWA only)
- In-browser timeline editing beyond shot boundaries (use DaVinci Resolve via FCPXML)
- Recording audio or video inside mhsquare
