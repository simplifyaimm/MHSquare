"""Convert an existing plan's free-form animation shots into scene templates.

Keeps timing, vocabulary and every finished shot; only unfinished free-form code_animation shots are rewritten.
A few batched model calls replace one expensive scene-writing call per shot. Shots no template fits keep their
free-form brief.
"""

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import get_sessionmaker
from app.llm import client as llm
from app.llm.errors import ErrorKind, LLMError
from app.models import Project, Shot, ShotState
from app.services import routing
from app.services.generation import templates as T
from app.services.planner import extract_json

log = logging.getLogger(__name__)

BATCH = 12
KEEP = {ShotState.review, ShotState.accepted, ShotState.queued, ShotState.generating}
_dispatch: dict = {}


def register_dispatch(fn) -> None:
    _dispatch["run"] = fn


SYSTEM = """You convert shots of an explainer video into SCENE TEMPLATES. Each shot has its narration and a
designer brief. For each shot pick the template that shows the idea best and fill its params with the EXACT
on-screen content. Almost every shot fits one (a person or situation → scenario, a list of points → checklist,
a process → steps, a comparison → compare, a number → stat, a request → flow, parts of a whole → hub). Return
null ONLY when no template can possibly show the idea (rare: under 1 in 5) — a custom scene costs far more.

Rules: short on-screen text (labels ≤ 4 words, no full sentences except `say`/`sub`), icons only from the icon
library, `entity` = an id from the vocabulary (it supplies label, icon and colour so a thing looks identical in
every shot), `on` = a word or 2–3-word phrase that is spoken in THAT shot's narration (the element appears on it).
Keep the brief's intent; use `scenario` for the running real-world example (persona = its vocabulary entity).

Pick by the SHAPE of what is said, not by the shot's mode (a "flow" mode does not mean the flow template):
- numbered steps, "first… then…", a procedure → steps
- weeks, phases, a roadmap, history → timeline
- "X vs Y", before/after, old way/new way → compare
- one striking number or percentage → stat
- a list of tips, requirements, things to learn → checklist
- one thing and its parts/options/tools → hub
- rows of facts with the same columns → table
- real code or a config → code
- requests/messages travelling between 2–4 systems → flow (ONLY then)
- a named person in a situation → scenario

VARIETY IS REQUIRED: never the same template in more than 2 consecutive shots, and no template in more than about
a third of the shots in a batch. If a neighbour already used a template, prefer a different one that still fits.
The direction's preferred templates are a preference, not a limit. `previous` gives the template of the shot just
before this batch.

Templates:
{templates}

Icon library: {icons}

Reply with ONLY JSON: {"shots": [{"index": 12, "template": {"name": "compare", "params": {...}}},
{"index": 13, "template": null}]}"""


def system_prompt() -> str:
    from app.services.generation.icons import names

    return SYSTEM.replace("{templates}", T.catalogue()).replace(
        "{icons}", ", ".join(names())
    )


def candidates(db: Session, p: Project) -> list[Shot]:
    shots = db.scalars(select(Shot).where(Shot.project_id == p.id).order_by(Shot.index))
    return [
        s
        for s in shots
        if s.visual_type == "code_animation"
        and s.state not in KEEP
        and not _is_template(s)
    ]


def _is_template(s: Shot) -> bool:
    try:
        return T.parse(s.prompt) is not None
    except T.TemplateError:
        return True  # already a template (with broken JSON the user can fix)


def _set_status(db: Session, p: Project, **kv) -> None:
    meta = dict(p.plan_meta or {})
    meta["templatize"] = {**(meta.get("templatize") or {}), **kv}
    p.plan_meta = meta
    flag_modified(p, "plan_meta")
    db.commit()


def begin(db: Session, p: Project) -> int:
    n = len(candidates(db, p))
    _set_status(
        db,
        p,
        status="running",
        total=n,
        done=0,
        converted=0,
        kept=0,
        cost_usd=0.0,
        error=None,
    )
    _dispatch["run"](str(p.id))
    return n


def _template_of(s: Shot | None) -> str | None:
    try:
        return (T.parse(s.prompt) or (None,))[0] if s else None
    except T.TemplateError:
        return None


def _user_message(p: Project, batch: list[Shot], previous: str | None = None) -> str:
    sg = p.style_guide or {}
    vocab = [
        {k: e.get(k) for k in ("id", "label", "icon", "color", "note")}
        for e in sg.get("entities", [])
    ]
    shot_ents = sg.get("shot_entities") or {}
    rows = [{"index": s.index, "mode": s.theme, "seconds": round(s.duration_ms / 1000, 1),
             "narration": s.transcript_text, "brief": s.prompt[:1500],
             "entities": shot_ents.get(str(s.index), [])} for s in batch]  # fmt: skip
    from app.services.direction import brief

    direction = brief(p.direction)
    return (f"Video: {sg.get('summary', '')}\n{direction}\nVocabulary: {json.dumps(vocab, ensure_ascii=False)}\n"
            f"previous: {previous or 'none'}\n\n"
            f"Shots:\n{json.dumps(rows, ensure_ascii=False, indent=1)}")  # fmt: skip


def _apply(db: Session, batch: list[Shot], reply: dict) -> tuple[int, int]:
    by_index = {s.index: s for s in batch}
    converted = 0
    for item in reply.get("shots") or []:
        s = by_index.get(item.get("index"))
        tpl = item.get("template")
        if s is None or not isinstance(tpl, dict):
            continue
        try:
            T.validate(str(tpl.get("name")), tpl.get("params") or {})
        except T.TemplateError as ex:
            log.info("shot %s stays free-form: %s", s.index, ex)
            continue
        brief = s.prompt
        s.prompt = T.to_prompt(tpl["name"], tpl["params"])
        s.notes = (f"{s.notes} · brief: {brief}" if s.notes else f"brief: {brief}")[
            :2000
        ]
        if s.state in (ShotState.failed, ShotState.paused_budget):
            s.state, s.error = ShotState.planned, None
        converted += 1
    db.commit()
    return converted, len(batch) - converted


def run(project_id: str) -> None:
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        cfg = routing.load()
        todo = candidates(db, p)
        done = converted = kept = 0
        cost = 0.0
        try:
            for i in range(0, len(todo), BATCH):
                batch = todo[i : i + BATCH]
                prev = db.scalar(
                    select(Shot).where(
                        Shot.project_id == p.id, Shot.index == batch[0].index - 1
                    )
                )
                msg = _user_message(p, batch, _template_of(prev))
                text, res = llm.chat(cfg.planner.model, [{"role": "user", "content": msg}],
                                     system=system_prompt(), fallbacks=cfg.planner.fallbacks, max_tokens=24000,
                                     temperature=0.3)  # fmt: skip
                cost += res.cost_usd
                try:
                    c, k = _apply(db, batch, extract_json(text))
                except (ValueError, KeyError) as ex:
                    log.warning("templatize batch unreadable: %s", ex)
                    c, k = 0, len(batch)
                done, converted, kept = done + len(batch), converted + c, kept + k
                _set_status(
                    db,
                    p,
                    done=done,
                    converted=converted,
                    kept=kept,
                    cost_usd=round(cost, 4),
                )
            _set_status(db, p, status="done")
        except LLMError as ex:
            db.rollback()
            p = db.get(Project, pid)
            paused = ex.kind == ErrorKind.budget
            _set_status(db, p, status="paused" if paused else "failed", error=str(ex)[:500],
                        cost_usd=round(cost, 4))  # fmt: skip
        except Exception as ex:  # noqa: BLE001
            log.exception("templatize failed")
            db.rollback()
            p = db.get(Project, pid)
            _set_status(
                db, p, status="failed", error=f"{type(ex).__name__}: {ex}"[:500]
            )
