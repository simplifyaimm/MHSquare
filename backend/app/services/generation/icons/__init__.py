"""Fixed line-icon library (Lucide, ISC — see LICENSE-lucide.txt). Every scene draws entities with the SAME
icons, so a video never mixes hand-drawn icon styles."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache
def icons() -> dict[str, str]:
    return json.loads((Path(__file__).parent / "lucide.json").read_text("utf-8"))


def names() -> list[str]:
    return sorted(icons())


def runtime_js() -> str:
    """Injected before scene code: `<i data-icon="database"></i>` → inline SVG; `mhIcon(name)` → svg markup."""
    return (
        "window.MH_ICONS = " + json.dumps(icons()) + ";\n"
        "window.mhIcon = function (name, cls) { const p = window.MH_ICONS[name];\n"
        "  if (!p) throw new Error('unknown icon ' + name);\n"
        '  return \'<svg class="mh-ic \' + (cls || \'\') + \'" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">\' + p + \'</svg>\'; };\n'
        "document.querySelectorAll('#stage [data-icon]').forEach(el => { el.innerHTML = mhIcon(el.dataset.icon); });\n"
    )
