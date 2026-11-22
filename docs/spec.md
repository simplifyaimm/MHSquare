# mhsquare — merged specification

Layers, newest wins on conflict:

1. **v1** — [mhsquare-build-spec.md](../mhsquare-build-spec.md) (base pipeline, phases 1–8)
2. **v2** — `mhsquare-update-v2.md` — **not yet in the repo.** Script Studio, Shorts Studio, Publish Kit, Brand Kit and the
   Descript round-trip are specified there. v2.1 parts that depend on it are listed under "Pending" below.
3. **v2.1** — [mhsquare-update-v2.1-style.md](../mhsquare-update-v2.1-style.md) (Simplify AI Style Profile), reproduced below.

## Implementation status of v2.1

| v2.1 section | Status |
|---|---|
| §1 Global rules (English only, brand, pillars, watermark `@simplifyai.mm`, no hype) | ✅ seeded Style Profile, editable + versioned (Brand page) |
| §2 Voice & teaching rules + judge rubric with English-only fail | ✅ script check on the uploaded script (deterministic + LLM judge) |
| §3 Content-type presets, audience toggle, Reach/Authority + 2:1 ratio | ✅ project fields, presets in profile, ratio chip on Projects |
| §3.1 Long-form layout (no a_roll, face-cam circle bottom-right, safe area, watermark top-right) | ✅ planner rule, render-time safe-area probe, preview compositing (`composite` / `leave_space`) |
| §3.2 Shorts canvas (3:4 in 1080×1920, or 9:16), length warning > ~180 words | ✅ canvas per content type, judge length warning |
| §4 Topic registry + semantic repeat warning + seed list + auto-add on final | ✅ Topics page, embeddings via LiteLLM (lexical fallback), "Mark script final" |
| §5 `html_animation` generator (Playwright, virtual time, MP4), brand fonts, style rules, footer, VS Code (Windows) code demos, style lint + one fix round | ✅ |
| §6 Suggest reply (agree → amplify → tie back), caption formula placeholder | ✅ reply helper on the Brand page; caption formula stored |
| §6 Thumbnails / covers | ✅ cover generator (YouTube 1280×720 + Shorts/Reels 1080×1920) with creator cut-out |
| §6 Publish Kit descriptions, pinned comment, hashtags | ⏳ pending v2 (Publish Kit) |
| §7 Script Studio judge, Shorts Studio vertical re-render, Descript round-trip | ⏳ pending v2 (judge + canvas + lint are ready to be reused) |
| §8 Data model (StyleProfile, Project fields, CoveredTopic) | ✅ migration 0004 |

---

# mhsquare — v2.1 Update: Simplify AI Style Profile

> **Claude Code:** apply this on top of `mhsquare-build-spec.md` (v1) and `mhsquare-update-v2.md` (v2).
> Where this file conflicts with v1 or v2, **this file wins**. Merge it into `docs/spec.md` and log the changes in `docs/decisions.md`.
> The style rules below become the **seeded defaults of the Brand Kit / Style Profile**. They are stored in the DB, editable in the UI, versioned, and **injected into every prompt** that writes words or visuals:
> - Script Studio
> - shot planner
> - code/HTML animation generation
> - thumbnails
> - Shorts hook text
> - Publish Kit
>
> Do not hard-code them in prompts. Load them from the active Style Profile version.

---

## 1. Global rules (all platforms)

- **Language: English only** for every script: YouTube long-form, Shorts, Instagram Reels, captions, descriptions, pinned comments, thumbnails and on-screen text.
  - No Hinglish and no Hindi lines.
  - This replaces any earlier language-mix rules.
  - The Style Profile has a `language` field, locked to `en` by default.
  - Script Studio's judge must **fail** any draft that contains Hindi or Hinglish.
- **Brand:**
  - name: Simplify AI
  - tagline: **"No hype. Real systems."**
  - handle: **@simplifyai.mm**
- **Watermark text default: `@simplifyai.mm`.** This matches the handle used in existing animation footers. It replaces v2's `simplify.mm` default and stays editable in the Brand Kit.
- **Brand pillars** (every script, short and thumbnail is tagged with one):

| Pillar | Colour |
|---|---|
| Series | teal `#48cae4` |
| Growth | red `#e63946` |
| Tools | purple `#9d4edd` |
| Advice | blue (UI token `--pillar-advice`, default `#3a86ff`, editable) |

- **No hype.** No clickbait claims, no "this will change everything", no invented numbers. State things plainly and accurately.

---

## 2. Voice and teaching style (locked default)

Every script follows these rules. They become the Script Studio system prompt, and the judge scores against them.

1. **Conversational teacher-to-student.** Warm and relatable. Talk to one student, not an audience.
2. **Build intuition step by step.** Use rhetorical questions to move the explanation forward ("So what happens when…?", "Why does this matter?").
   - Natural English cue phrases are allowed: "look", "imagine", "think about it", "here's the thing".
   - No Hindi cue words.
3. **Actually explain how the thing works.** Show the mechanism, not just a definition. Thin, surface-level scripts are a failure.
4. **At least one concrete real-world example, carried from start to finish.** It must be a real scenario, not only an analogy. Analogies are allowed as support, but they never replace the real example.
5. **End with a clear takeaway** the viewer can remember or act on.
6. **"Natural" means** conversational tone plus a carried example. It does not just mean simplified language.
7. **India-specific angle where it genuinely fits**: costs in ₹, Indian job market, Indian company or enterprise context. Don't force it.
8. **Default audience: students and beginners new to AI.**
   - Script Studio has an **Audience level** toggle: `beginner` (default) / `advanced`.
   - Use `advanced` only when I select it.

### Judge rubric (replaces v2's rubric)

Score each criterion 1–10 with a one-line reason:

- Hook in the first 15 s
- **Explains the mechanism** (how it works, not just what it is)
- **Carried real-world example** (present start to finish)
- Clear takeaway
- Beginner clarity (or advanced depth if that's selected)
- Spoken flow: short sentences, easy to say aloud
- No hype / accuracy
- Pacing vs. target length
- **English-only check:** pass/fail. Fail means the draft is rejected.

---

## 3. Content-type presets

Script Studio starts with a **Content type** selection. Each preset sets the length target, the rules and the visual defaults.

### 3.1 YouTube long-form

- English, natural and conversational, **very detailed but easy enough for students**.
- **No presenter intro and no first-person credibility lines.**
  - Banned examples: "Hi, I'm…", "I've been writing Java for 15 years".
  - Narrate in **third person around a character called "X"**: "X is building a chatbot for…". X carries the real-world example through the video.
  - The judge flags first-person credibility lines and presenter intros.
- Length: from the target-minutes input. No hard cap.
- Topic type tag: **Reach** (broad demand: jobs, salaries, time-saving tools, "should I learn X") or **Authority** (deep teaching, such as the RAG series). The Project list shows the running Reach : Authority ratio, with **2 : 1** as the target.
- **Video layout (long-form):**
  - The full frame always shows visuals (animations or screen share).
  - My face cam is always a **small circle in the bottom-right**.
  - The planner must **never plan full-screen `a_roll` shots for long-form**. The face cam is a persistent picture-in-picture layer, not a shot type.
  - Every generated visual keeps a **bottom-right safe area** (default 22% width × 22% height) free of important content.
  - Watermark goes **top-right**, so it never collides with the face-cam circle.
  - Assembly composites the face-cam circle when a face-cam file is provided. Setting: `facecam_circle: composite | leave_space` (default `composite`). With `leave_space`, Descript adds the circle and mhsquare only keeps the safe area clear.

### 3.2 Shorts — Series / Growth / Tools

- **Recorded length ≤ ~1.2 minutes.** Script Studio warns above ~180 spoken words at 150 WPM, with the WPM configurable.
- Beginner-friendly by default.
- Must still explain how the thing works, use a real-world example and give a clear takeaway. Short does not mean thin.
- **Full-canvas animation:** the animation fills the whole frame, with **no face** during animation segments.
  - Animation canvas aspect is a Style Profile setting, default **3:4** per my current shorts spec.
  - The final export is 1080×1920, with the 3:4 canvas placed and the remaining area filled with the brand background.
  - If I switch the setting to 9:16, animations fill the full frame.
- Topics can include concepts **and** practical frameworks, languages and tools: Python for AI, LangChain, LangGraph, AI coding assistants, and so on.

### 3.3 Shorts — Advice

- Genuine, experience-based career advice. **First person is allowed here**, and only here.
- Same length cap, English only.

### 3.4 Instagram Reels

- Same rules as the matching Shorts preset (Series/Growth/Tools or Advice).
- Captions follow the Publish Kit rules in section 6.

---

## 4. Topic registry (no repeats)

- Keep a **Covered Topics** list in the DB, editable in the UI.
- Script Studio and Shorts Studio check new topics against it using semantic similarity (embeddings via LiteLLM). They **warn me about likely repeats** before generating.
- Seed list (I'll extend it in the UI):
  - NotebookLM (Tools short)
  - Claude Code subagents (Tools short)
  - Cursor @Git (Tools short)
  - Agent tool overload (Growth short)
  - RAG quality (Growth short)
  - Zero to Hero series episodes #1–19 (through function calling)
  - LLM Observability (long-form)
  - Hybrid Search, Reranking, RAG Evaluation, Agentic RAG, MCP, Fine-Tuning vs RAG, GraphRAG (8-week plan topics; mark each as covered or planned in the UI)
- When a script is marked final, its topic is added to the registry automatically.

---

## 5. Visual style for generated animations (locked default)

### 5.1 Use HTML animations as the main code-animation generator

My existing animations are HTML/CSS/JS, so add a new generator: **`html_animation`**. Make it the **default for `code_animation`** in `routing.yaml`, and keep Manim as an alternative (`manim_code`).

1. The LLM writes a self-contained HTML/CSS/JS animation using the style rules below.
2. Render it in the sandbox with **headless Chromium (Playwright)**:
   - deterministic frame capture at the exact stage size and project fps
   - virtual time, so animations are frame-accurate
   - output to MP4
3. Same sandbox rules as Manim:
   - no network
   - fonts bundled locally (Oswald, Poppins, JetBrains Mono, Consolas fallback)
   - CPU/memory limits
   - timeout
4. This replaces manual OBS recording of animations. The output is pixel-exact at the stage size, so there's no scaling blur.

### 5.2 Style rules (injected into every animation prompt)

- **Stage:** fixed pixel stage at the export resolution, absolute positioning. Scale it with `scale = Math.min(winH/H, winW/W)`, `transform-origin: center center`.
- **Background:** navy gradient `#0a1626` → `#060b14` with a **54 px blueprint grid**.
- **Colours:**

| Use | Colour |
|---|---|
| Series | teal `#48cae4` |
| Growth | red `#e63946` |
| Tools | purple `#9d4edd` |
| Warnings | amber `#f4a261` |
| Success | green `#2dd4a7` |

  The accent colour follows the project's pillar.
- **Fonts:** **Oswald** headlines, **Poppins** body, **JetBrains Mono** for meta labels.
- **Elements:** pill and LED components. **4–6 s staggered entry, then hold the final frame.** Auto-play on load.
- **Explain with visuals and diagrams, not text filling the screen.** Use a mix, leaning visual.
  - Each animation must genuinely help explain its line. Decorative filler is rejected.
- **Nothing on the stage except the animation:** no replay buttons, toggles, dashed borders, guide marks or person placeholders.
- **Footer:** "NO HYPE. REAL SYSTEMS." on the left, "@simplifyai.mm" on the right.
  - Setting `footer_on_animations` (default on).
  - When the footer is on, the top-corner watermark is **off** for that shot by default, so the handle doesn't appear twice.
- **Code demos: VS Code on Windows style:**
  - title bar with minimize/maximize/close **on the right** (never macOS dots on the left)
  - **Consolas** font
  - `#1e1e1e` background, Dark+ syntax colours
  - `#007acc` status bar
- **Layout-specific rules:**
  - Long-form: keep the bottom-right face-cam safe area clear.
  - Shorts: full-canvas animation per section 3.2.

Add a **style lint** step after generation. The LLM-written HTML is checked automatically for:
- colours outside the palette
- missing footer
- macOS window dots in code demos
- on-stage control elements
- fonts other than the allowed ones

On failure, send the issues back to the LLM once to fix.

---

## 6. Thumbnails, captions and Publish Kit

- **Thumbnails and covers:**
  - English headline of ≤ 4 words, with **technical keywords highlighted** in the pillar colour (for example **RAG**, **AGENT**, **MCP**).
  - Brand fonts (Oswald for the headline), navy brand background style where it fits.
  - The long-form YouTube thumbnail is 1280×720. Shorts and Reels covers are 1080×1920.
- **Descriptions and captions (rule from v2, unchanged):** never restate the script. Teach beyond the video: deeper explanation, variations, trade-offs, pitfalls, practical tips.
- **Save-optimised caption formula** and **comment-reply framework (agree → amplify → tie back)** are editable text templates in the Brand Kit.
  - Publish Kit uses the caption formula for Instagram/Shorts captions.
  - Add a **"Suggest reply"** helper: I paste a viewer comment and it drafts a reply using agree → amplify → tie back.
  - I'll fill in the exact caption formula wording in the UI. Leave a clearly marked placeholder in the seed data.
- Pinned comment and hashtags: English only.

---

## 7. Changes to the v1/v2 pipeline

- **Planner:**
  - Reads the content-type preset and the Style Profile.
  - Long-form: no `a_roll` shots, face-cam safe area required.
  - Shorts: full-canvas animation, no face during animation segments.
  - `code_animation` defaults to `html_animation`.
- **routing.yaml:**

```yaml
visual_types:
  code_animation:
    generator: html_animation     # default (headless Chromium capture)
    alt_generator: manim_code
    llm_model: claude-code-gen
    fallbacks: [claude-code-gen-backup]
```

- **Assembly:**
  - Composite the face-cam circle bottom-right for long-form (when `facecam_circle: composite`).
  - Watermark top-right, following the footer rule in section 5.2.
- **Shorts Studio (v2):**
  - Vertical re-renders of `html_animation` shots re-render the same HTML at the Shorts canvas size.
  - If the layout breaks, the LLM adapts it once, and style lint runs again.
- **Descript round-trip (v2):** `mhsquare_visuals.mp4` keeps the face-cam safe area empty when `facecam_circle: leave_space`.

---

## 8. Data model additions

- `StyleProfile`: id, version, language (`en`), voice_rules (text), judge_rubric (JSON), audience_default, presets (JSON per content type), animation_style (JSON: colours, fonts, grid, timings, footer, code-demo style), layout (JSON: facecam_circle mode, safe areas, shorts canvas aspect), caption_formula, reply_framework
- `Project`: add `content_type` (long_form | short_series | short_growth | short_tools | short_advice | reel), `pillar`, `topic_kind` (reach | authority), `audience_level`
- `CoveredTopic`: id, title, pillar, content_type, status (covered | planned), embedding, source_project_id

---

## 9. Build phase

**Phase 10b — Style Profile (do it right after Phase 10, Script Studio)**
- StyleProfile model and UI, with the seeded defaults above.
- Content-type presets, updated judge rubric with the English-only fail check and first-person checks.
- Topic registry with seed data and repeat warnings.
- `html_animation` generator with Playwright frame capture, style lint, face-cam safe area, face-cam circle compositing, and the footer/watermark rule.

*Done when:*
- A long-form draft with "Hi, I'm…" or a Hindi line is flagged by the judge.
- A Shorts draft over ~180 words shows the length warning.
- A generated HTML animation renders pixel-exact with brand colours, fonts and footer, and passes style lint.
- Proposing "NotebookLM tips" as a new topic triggers a repeat warning.
