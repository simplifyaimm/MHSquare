"""Script judge (spec v2.1 §2, §3): deterministic checks + an LLM judge scored against the active
Style Profile's rubric. English-only failure (deterministic OR judge) rejects the draft.

Script Studio (v2) will reuse this; for now it checks an uploaded script.
"""

import json
import re

from app.llm import client as llm
from app.models import Project
from app.models.style import StyleProfile
from app.services import routing
from app.services.planner import extract_json
from app.services.style import is_short, preset

DEVANAGARI = re.compile(r"[ऀ-ॿ]+")
# Common Hinglish (romanised Hindi) words that don't collide with English vocabulary.
HINGLISH = {"hai", "hain", "kya", "nahi", "nahin", "matlab", "yaar", "accha", "acha", "bhai", "toh", "kaise", "haan",
            "theek", "thik", "samjho", "samjhe", "dekho", "chalo", "kyunki", "lekin", "aur", "bahut", "bohot", "mera",
            "meri", "tera", "tumhe", "aapko", "hota", "hoti", "karo", "karna", "raha", "rahi", "wala", "wali",
            "abhi", "kuch", "jaise", "isliye", "phir", "kyu", "kyun", "samajh", "dekhiye", "chaliye"}  # fmt: skip
HINGLISH_MIN_HITS = 2  # one stray token ("ji", "na") isn't enough
INTRO = re.compile(
    r"\b(hi|hello|hey|namaste)\b[\s,!.]*(guys|everyone|folks|friends)?[\s,!.]*(i'?m|i am|this is|my name is|welcome)",
    re.I,
)
CREDIBILITY = [
    re.compile(r"\bI(?:'ve| have) been\b[^.!?\n]{0,60}\b(for|since)\s+(\d+|a decade|years)", re.I),
    re.compile(
        r"\bI(?:'m| am) an?\s+(senior|staff|principal|lead|experienced)\b[^.!?\n]{0,40}"
        r"\b(engineer|developer|architect|scientist)",
        re.I,
    ),  # fmt: skip
    re.compile(r"\b(\d+|fifteen|ten|twenty)\+?\s+years of (experience|writing|building|working)", re.I),
    re.compile(r"\bin my (\d+|many)\s+years\b", re.I),
]


def spoken_text(md: str) -> str:
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"^\s*#+\s*", "", md, flags=re.M)
    md = re.sub(r"[*_`>]", "", md)
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)


def deterministic(text: str, content_type: str, profile: StyleProfile) -> dict:
    words = re.findall(r"[A-Za-zऀ-ॿ0-9'’]+", text)
    ps = preset(profile, content_type)
    wpm = int(ps.get("wpm") or 150)
    flags: list[dict] = []
    deva = DEVANAGARI.findall(text)
    hing = sorted({w.lower() for w in words if w.lower() in HINGLISH})
    hing_hits = sum(1 for w in words if w.lower() in HINGLISH)
    english_ok = not deva and hing_hits < HINGLISH_MIN_HITS
    if deva:
        flags.append(
            {"kind": "hindi", "severity": "fail", "message": f"Hindi (Devanagari) text: {' '.join(deva[:5])}"}
        )
    if hing_hits >= HINGLISH_MIN_HITS:
        flags.append(
            {"kind": "hinglish", "severity": "fail", "message": f"Hinglish words: {', '.join(hing[:10])}"}
        )
    if not ps.get("first_person", False):
        for m in INTRO.finditer(text):
            flags.append(
                {
                    "kind": "presenter_intro",
                    "severity": "fail",
                    "message": f"Presenter intro: “{_ctx(text, m)}”",
                }
            )
        for rx in CREDIBILITY:
            for m in rx.finditer(text):
                flags.append({"kind": "first_person_credibility", "severity": "fail",
                              "message": f"First-person credibility line: “{_ctx(text, m)}”"})  # fmt: skip
    warn_words = ps.get("warn_words")
    if is_short(content_type) and warn_words and len(words) > int(warn_words):
        flags.append({"kind": "length", "severity": "warn",
                      "message": f"{len(words)} words ≈ {len(words) / wpm:.1f} min at {wpm} WPM — over the ~{warn_words}-word"
                                 f" short limit (≤ {ps.get('max_minutes', 1.2)} min)"})  # fmt: skip
    return {"words": len(words), "est_minutes": round(len(words) / wpm, 2), "wpm": wpm, "english_only": english_ok,
            "flags": flags}  # fmt: skip


def _ctx(text: str, m: re.Match) -> str:
    return text[max(0, m.start() - 10) : m.end() + 30].replace("\n", " ").strip()


JUDGE_SYSTEM = """You are the script judge for {brand} (“{tagline}”). Judge a script for a {ctype} video against the
voice and teaching rules below. Be strict and specific: one short reason per criterion, quoting the script where useful.

## Voice and teaching rules
{voice}

## Content-type preset
{preset_rules}

Audience level: {audience}. Topic: {topic}

## Rubric
{rubric}

Score every "score" criterion 1–10. "pass_fail" criteria get "pass" or "fail".
Reply with ONLY JSON:
{{"scores": [{{"key": "...", "score": 7, "reason": "..."}}, {{"key": "english_only", "result": "pass", "reason": "..."}}],
  "issues": ["most important fixes, max 5"], "summary": "one line"}}"""


def judge(project: Project, profile: StyleProfile, script_md: str) -> dict:
    text = spoken_text(script_md)
    det = deterministic(text, project.content_type, profile)
    ps = preset(profile, project.content_type)
    rubric = "\n".join(
        f"- {r['key']}: {r['label']} ({r.get('kind', 'score')})" for r in profile.judge_rubric or []
    )
    system = JUDGE_SYSTEM.format(
        brand=(profile.brand or {}).get("name"), tagline=(profile.brand or {}).get("tagline"),
        ctype=ps.get("label", project.content_type), voice=profile.voice_rules, preset_rules=ps.get("rules", ""),
        audience=project.audience_level, topic=project.topic or "(not set)", rubric=rubric,
    )  # fmt: skip
    cfg = routing.load()
    text_out, res = llm.chat(cfg.planner.model, [{"role": "user", "content": f"SCRIPT:\n{script_md[:30000]}"}],
                             system=system, fallbacks=cfg.planner.fallbacks, max_tokens=3000, temperature=0.2)  # fmt: skip
    try:
        j = extract_json(text_out)
    except (ValueError, json.JSONDecodeError):
        j = {"scores": [], "issues": ["judge returned unreadable output"], "summary": ""}
    scores = j.get("scores", [])
    judge_english = next((s.get("result") for s in scores if s.get("key") == "english_only"), "pass")
    english_pass = det["english_only"] and judge_english != "fail"
    fails = [f for f in det["flags"] if f["severity"] == "fail"]
    numeric = [s.get("score") for s in scores if isinstance(s.get("score"), int | float)]
    avg = round(sum(numeric) / len(numeric), 1) if numeric else None
    hard_ok = english_pass and not fails
    return {
        "passed": hard_ok,
        # verdict: hard rules first, then quality (average rubric score)
        "verdict": "rejected" if not hard_ok else ("needs_work" if avg is not None and avg < 6 else "good"),
        "english_only": "pass" if english_pass else "fail",
        "deterministic": det,
        "scores": scores,
        "average": avg,
        "issues": j.get("issues", []),
        "summary": j.get("summary", ""),
        "style_profile_version": profile.version,
        "cost_usd": res.cost_usd,
    }


REPLY_SYSTEM = """You draft replies to viewer comments for {brand} ({handle}), tagline “{tagline}”.
Follow this framework exactly:
{framework}

Voice: warm teacher, English only, no hype, no emojis spam (max 1), 1–4 short sentences.
Reply with ONLY the reply text."""


def suggest_reply(profile: StyleProfile, comment: str, context: str = "") -> tuple[str, float]:
    b = profile.brand or {}
    system = REPLY_SYSTEM.format(brand=b.get("name"), handle=b.get("handle"), tagline=b.get("tagline"),
                                 framework=profile.reply_framework)  # fmt: skip
    user = f"Viewer comment:\n{comment.strip()}\n" + (
        f"\nVideo context:\n{context.strip()}" if context.strip() else ""
    )
    cfg = routing.load()
    text, res = llm.chat(cfg.planner.model, [{"role": "user", "content": user}], system=system,
                         fallbacks=cfg.planner.fallbacks, max_tokens=600, temperature=0.6)  # fmt: skip
    return text.strip().strip('"'), res.cost_usd
