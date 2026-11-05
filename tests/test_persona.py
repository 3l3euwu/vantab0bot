"""Persona file loading, style resolution and placeholder rendering."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import ROOT

from vantab.persona import DEFAULT_PERSONA, Persona, load_persona


def make_persona(**overrides) -> Persona:
    data = {
        "bot_name": "Vantab0t",
        "bot": "helper owo",
        "nom_utilisateur": "{discord user}",
        "style_actif": "elee",
        "salutation": "hewoooooo~ cha vaaa~ ? :3",
        "styles": {
            "elee": "Tu es {bot}, mignonne. tu parles à {nom_utilisateur}~",
            "normal": "Tu es {bot}, un assistant utile.",
        },
    }
    data.update(overrides)
    return Persona.from_dict(data)


def test_persona_fields() -> None:
    persona = make_persona()
    assert persona.bot_name == "Vantab0t"
    assert persona.description == "helper owo"
    assert persona.greeting == "hewoooooo~ cha vaaa~ ? :3"
    assert persona.default_style == "elee"
    assert persona.style_names == ["elee", "normal"]


def test_resolve_style() -> None:
    persona = make_persona()
    assert persona.resolve_style("normal") == "normal"
    assert persona.resolve_style(" NORMAL ") == "normal"
    assert persona.resolve_style("nope") == "elee"
    assert persona.resolve_style(None) == "elee"


def test_render_placeholders() -> None:
    persona = make_persona()
    rendered = persona.render("{bot} / {bot_name} / {user} / {discord user} / {role} / {salutation}", user_name="alice")
    assert rendered == "Vantab0t / Vantab0t / alice / alice / helper owo / hewoooooo~ cha vaaa~ ? :3"
    # Unknown user falls back to the persona's placeholder.
    assert persona.render("{user}") == "{discord user}"


def test_system_prompt_includes_context() -> None:
    persona = make_persona()
    prompt = persona.system_prompt(style="normal", user_name="bob")
    assert prompt.startswith("Tu es Vantab0t, un assistant utile.")
    assert "bob" in prompt
    assert "contexte" in prompt


def test_system_prompt_extra() -> None:
    persona = make_persona()
    prompt = persona.system_prompt(style="normal", extra="salue {user}")
    assert prompt.endswith("salue {discord user}")


def test_greeting_override() -> None:
    persona = make_persona()
    assert persona.greeting_text("hey {user}~", user_name="alice") == "hey alice~"
    assert persona.greeting_text(None, user_name="alice") == "hewoooooo~ cha vaaa~ ? :3"


def test_invalid_persona_raises() -> None:
    import pytest

    from vantab.persona import PersonaError

    with pytest.raises(PersonaError):
        Persona.from_dict({"styles": {}})
    with pytest.raises(PersonaError):
        Persona.from_dict({})


def test_loads_shipped_persona() -> None:
    persona = load_persona(ROOT / "prompts" / "persona.json")
    assert persona.bot_name == "Vantab0t"
    assert persona.default_style == "elee"
    assert "minuscules" in persona.styles["elee"]
    assert "kaomoji" in persona.styles["elee"]
    assert persona.greeting.startswith("hewoooooo~")


def test_shipped_json_is_valid() -> None:
    data = json.loads((ROOT / "prompts" / "persona.json").read_text(encoding="utf-8"))
    assert data["style_actif"] in data["styles"]
    assert data["bot_name"] == "Vantab0t"


def test_missing_file_falls_back(tmp_path: Path) -> None:
    persona = load_persona(tmp_path / "nope.json")
    assert persona.bot_name == DEFAULT_PERSONA["bot_name"]


def test_invalid_file_falls_back(tmp_path: Path) -> None:
    broken = tmp_path / "persona.json"
    broken.write_text("{not json", encoding="utf-8")
    persona = load_persona(broken)
    assert persona.default_style == "elee"


def test_cached_loading() -> None:
    path = ROOT / "prompts" / "persona.json"
    assert load_persona(path) is load_persona(path)
