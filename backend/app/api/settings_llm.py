"""Settings → AI providers. Secrets are write-only: responses carry only masked hints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.llm import client, settings_store
from app.llm.settings_store import MODEL_SLOTS, PROVIDERS, PROVIDERS_BY_ID, SLOTS_BY_ALIAS, LLMConfigError

router = APIRouter(prefix="/settings/llm", tags=["settings"], dependencies=[Depends(get_current_user)])


class LLMSettingsUpdate(BaseModel):
    # provider id -> new key; "" or null clears it. Omitted providers are unchanged.
    provider_keys: dict[str, str | None] = Field(default_factory=dict)
    # alias -> model id; "" or null resets to the default.
    models: dict[str, str | None] = Field(default_factory=dict)


class BudgetUpdate(BaseModel):
    daily_usd: float = Field(gt=0, le=10_000)


def _litellm_status() -> str:
    try:
        return client.litellm_health()
    except Exception:  # noqa: BLE001
        return "unreachable"


def _view() -> dict[str, Any]:
    raw = settings_store.read_raw()
    defaults = settings_store.read_defaults()
    models = settings_store.effective_models()
    configured = {p.id for p in PROVIDERS if raw.get(p.env_var)}

    budget: dict[str, Any] = {"configured": settings_store.virtual_key() is not None}
    try:
        info = client.key_info()
        if info:
            budget.update(info)
    except client.LiteLLMUnavailable:
        budget["unavailable"] = True

    return {
        "litellm": _litellm_status(),
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "env_var": p.env_var,
                "docs": p.docs,
                "configured": p.id in configured,
                "hint": settings_store.mask(raw.get(p.env_var)),
            }
            for p in PROVIDERS
        ],
        "models": [
            {
                "alias": s.alias,
                "label": s.label,
                "kind": s.kind,
                "model": models[s.alias],
                "default": defaults.get(s.env_var, ""),
                "is_default": s.env_var not in raw,
                "provider": next((p.id for p in PROVIDERS if models[s.alias].startswith(p.prefix)), None),
                "provider_configured": any(
                    models[s.alias].startswith(p.prefix) and p.id in configured for p in PROVIDERS
                ),
                "suggestions": list(s.suggestions),
            }
            for s in MODEL_SLOTS
        ],
        "budget": budget,
    }


@router.get("")
def get_llm_settings():
    return _view()


@router.put("")
def put_llm_settings(body: LLMSettingsUpdate):
    changes: dict[str, str | None] = {}
    try:
        for pid, value in body.provider_keys.items():
            p = PROVIDERS_BY_ID.get(pid)
            if p is None:
                raise LLMConfigError(f"Unknown provider {pid}")
            changes[p.env_var] = settings_store.validate_secret(value) if value else None
        for alias, value in body.models.items():
            s = SLOTS_BY_ALIAS.get(alias)
            if s is None:
                raise LLMConfigError(f"Unknown model alias {alias}")
            changes[s.env_var] = settings_store.validate_model(value) if value else None
    except LLMConfigError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    if changes:
        settings_store.update(changes)
    view = _view()
    view["restarting"] = bool(changes)  # LiteLLM reloads within a few seconds
    return view


@router.put("/budget")
def put_budget(body: BudgetUpdate):
    try:
        result = client.ensure_budget_key(body.daily_usd)
    except client.LiteLLMUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LiteLLM is not reachable") from e
    except client.LiteLLMAdminError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {**_view(), **result}


@router.post("/test/{alias}")
def test_model(alias: str):
    s = SLOTS_BY_ALIAS.get(alias)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown alias")
    if s.kind != "llm":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only LLM aliases can be tested cheaply")
    if not settings_store.virtual_key():
        raise HTTPException(status.HTTP_409_CONFLICT, "Set a daily budget first (creates the mhsquare key)")
    try:
        return client.test_alias(alias)
    except client.LiteLLMUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LiteLLM is not reachable") from e
