"""Render the rework-dispatch prompt from the Jinja2 source.

STORY-1009: kept as a tiny loader so the template lives in a reviewable
`.md.j2` file beside this module instead of being inlined into the route
handler. The template is rendered with Jinja2 when available; we fall back
to a built-in renderer so the dependency is optional for the unit-test path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_TEMPLATE_PATH = Path(__file__).resolve().parent / "rework_prompt.md.j2"


def _render_with_jinja2(template_text: str, context: dict[str, Any]) -> str:
    from jinja2 import Template  # type: ignore[import-not-found]

    return Template(template_text).render(**context)


def _render_fallback(template_text: str, context: dict[str, Any]) -> str:
    """Minimal renderer for the small subset of Jinja2 features used in the template.

    Supports:
        {{ var }}                                 — variable interpolation
        {% if flag %}…{% endif %}                 — single-clause conditional
        {% for item in list %}…{% endfor %}       — simple for loop with {{ loop.index }}
        {{ var | lower }}                         — lower filter
    """
    text = template_text
    # If blocks ({% if flag -%}…{%- endif %} variants)
    import re

    def _resolve(expr: str) -> Any:
        expr = expr.strip()
        if "|" in expr:
            name, _, filt = expr.partition("|")
            value = context.get(name.strip())
            filt = filt.strip()
            if filt == "lower" and isinstance(value, str):
                return value.lower()
            return value
        return context.get(expr)

    def _replace_if(match: re.Match) -> str:
        flag_expr = match.group(1).strip()
        body = match.group(2)
        return body if context.get(flag_expr) else ""

    text = re.sub(
        r"\{% if (.+?) -?%\}(.*?)\{%-? endif %\}",
        _replace_if,
        text,
        flags=re.DOTALL,
    )

    def _replace_for(match: re.Match) -> str:
        item_name, list_name, body = match.group(1).strip(), match.group(2).strip(), match.group(3)
        items = context.get(list_name) or []
        out_lines: list[str] = []
        for idx, item in enumerate(items, start=1):
            local_body = body
            local_body = local_body.replace("{{ loop.index }}", str(idx))
            local_body = local_body.replace(f"{{{{ {item_name} }}}}", str(item))
            out_lines.append(local_body)
        return "".join(out_lines)

    text = re.sub(
        r"\{% for (\w+) in (\w+) -?%\}(.*?)\{%-? endfor %\}",
        _replace_for,
        text,
        flags=re.DOTALL,
    )

    def _replace_var(match: re.Match) -> str:
        return str(_resolve(match.group(1)) or "")

    text = re.sub(r"\{\{\s*(.+?)\s*\}\}", _replace_var, text)
    return text


def render_rework_prompt(context: dict[str, Any]) -> str:
    """Render the rework prompt template against the given context."""
    template_text = _TEMPLATE_PATH.read_text()
    try:
        return _render_with_jinja2(template_text, context)
    except ImportError:
        return _render_fallback(template_text, context)
