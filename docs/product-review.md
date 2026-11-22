# mhsquare — architecture & product review (towards a sellable product)

_Reviewer stance: senior AI engineer / architect. Date: 2026-11-22. Scope: the system as built (phases 1–5,
v2.1 Style Profile, explanation modes, covers). Goal: "best quality for best views", end-to-end creator workflow,
sellable later._

## 1. What is already product-grade

| Area | Why it holds up |
|---|---|
| **Provider isolation** | All model traffic goes through LiteLLM. The domain only knows aliases (`planner-llm`, `code-llm`, `image-gen`, `tts`, `embed`). Swapping vendors is a Settings change, not a code change. Keys never leave the proxy. |
| **Deterministic rendering** | LLM-written HTML/GSAP runs in a sandboxed headless Chromium: no network, every request aborted, virtual clock, seeded random. Output is frame-exact MP4. Re-renders are reproducible, so caching and QA are trustworthy. |
| **Quality gates in depth** | Static security checks → brand style lint → layout probe (safe areas, off-stage text) → **vision QA** (the model reviews its own frames) → human review with takes. Each gate gets at most one fix round, so cost is bounded. |
| **Brand as data** | The versioned Style Profile drives prompts, CSS, footer, lint, judge, planner rules and covers. Brand changes need no code, and every output records the profile version it used. |
| **Never pay twice** | Content-addressed cache per take; idempotent pipeline steps; state persisted before work starts; crash-safe Celery (acks_late). |
| **Test discipline** | 100+ backend tests with fake LLM/renderer plus real ffmpeg; UI tests; no test calls a paid provider. |

## 2. The quality problem you saw ("Meet Priya") — root cause and fix

**It was a design problem, not a model problem.** The cartoon mode asked the LLM to invent a second art direction
on the fly (a teal room, a hand-built character rig whose mouth was a scaled shape). Stronger models would draw a
better cartoon, but it would still not belong in a navy tech video. Consistency comes from **constraints**:

1. **One visual language per video.** Every mode shares the brand background, fonts, palette and footer. The
   cartoon mode is off by default and replaced by an on-brand **scenario** mode (personas in your glowing ring).
2. **Visual vocabulary.** The planner defines every recurring entity once (label, icon, colour role), and every
   scene must draw it identically.
3. **Fixed icon library** (Lucide, 96 icons) instead of model-drawn icons. Unknown icons fail the lint.
4. **Mode cap.** At most 3 explanation modes per video, one primary. Enforced by plan validation.
5. **Vision QA.** The model looks at 3 frames of each render against a checklist (readable, composed, on-brand,
   explains the line, mature) and fixes issues once.
6. **Your face-cam circle on every frame** (long-form and Shorts) with the accent ring + glow from your photos.
   That is the human anchor that makes it "someone explaining", not a faceless channel.

## 3. Do we need more API keys?

| Need | Recommendation | Impact on quality |
|---|---|---|
| Scene code (`code-llm`) | **Keep Anthropic.** Optionally set `code-llm` to the Opus model for long-form hero shots (≈4–5× cost per shot). Vision QA already gives most of the lift. | High |
| Planner (`planner-llm`) | Opus (already). | High (structure + vocabulary) |
| Images (`image-gen`) | **Optional.** Add credit to OpenAI (`gpt-image-2`) *or* a paid Gemini tier (Nano Banana / Imagen). Useful for cover background art and occasional illustrations. Covers already work without it. | Medium |
| Video (`video-gen`, Veo) | **Not recommended for this channel.** Generative video undermines the consistent brand look and costs about $0.40/s. Keep it for rare b-roll. | Low / negative |
| TTS | Free Gemini TTS is fine for drafts. You record the real narration. | — |
| Embeddings | Free Gemini embeddings: fine. | — |

**Bottom line:** no new keys are required. Budget for Opus on scene code for important videos, and optionally image
credit for richer cover art.

## 4. Gaps to close before selling it

| Priority | Gap | Plan |
|---|---|---|
| P0 | **Live updates & resilience** (phase 6): SSE progress, run objects, pause/resume on budget, auto-resume, restart-safe polling. | Next phase. |
| P0 | **Real cost accounting**: LiteLLM doesn't price some new model IDs, so budgets don't bind. | Custom pricing in the LiteLLM config + our own per-take ledger; hard stop in the budget service. |
| P0 | **Exports** (phase 7): FCPXML for DaVinci, SRT captions, project bundle; a single high-quality final render. | Next. |
| P1 | **Multi-tenant**: workspaces, users/roles, per-tenant Style Profiles, storage prefixes, rate limits, audit log. | Introduce `Workspace` FK everywhere; row-level filters; S3 storage. |
| P1 | **Billing & quotas**: per-workspace budget = LiteLLM team/key per tenant + Stripe metering. | LiteLLM supports teams/keys natively. |
| P1 | **Throughput**: rendering is CPU-bound (≈5–15 s of compute per second of video). | Horizontal renderer pool (stateless), queue priorities, GPU Chromium later; cache common sub-scenes. |
| P1 | **Observability**: structured logs, traces per take, cost and latency dashboards, QA score trends. | OpenTelemetry + Prometheus/Grafana; LiteLLM callbacks. |
| P2 | **Licensing review before selling**: GSAP's free licence has restrictions for tools competing with Webflow (legal check); WhisperX/pyannote model licences; fonts are OFL (fine); Lucide is ISC (fine). | Legal review; swap libraries if needed (e.g. Motion One / anime.js are MIT). |
| P2 | **Template marketplace**: modes, kits and cover templates as installable packages per brand. | Mode kits are already isolated CSS + docs, so they can be packaged. |
| P2 | **Evaluation harness**: golden scripts → automatic QA scores per release (regression guard for prompts and kits). | Store QA verdicts; nightly batch on a fixed script set. |

## 5. Quality roadmap (highest leverage first)

1. Vision QA everywhere (done) → add **cross-shot QA**: a contact sheet of the whole video reviewed for consistency
   and pacing before the final render.
2. **Shot-to-shot transitions** that keep shared entities in place (match-cut of the vocabulary entity), so cuts
   feel like one continuous explanation.
3. **Reusable scene blocks**: once a shot is accepted, its components (e.g. "Kafka hub") become references for later
   shots, giving pixel-identical continuity.
4. **A/B covers** with click-through feedback (YouTube Analytics API) feeding the cover prompt.
5. **Retention-aware planning**: hook in 15 s, pattern interrupts every 20–40 s, measured against audience
   retention graphs.
