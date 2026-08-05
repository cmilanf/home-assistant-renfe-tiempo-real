"""Consistency checks on the manifest, the strings and the translations.

These are the parts of the integration that no other test exercises and that
Home Assistant only complains about at runtime, with a missing translation shown
to the user as a raw key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import custom_components.renfe_tiempo_real as renfe
from custom_components.renfe_tiempo_real.const import DOMAIN, SERVICE_REFRESH

ROOT = Path(renfe.__file__).parent
REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "codeowners",
    "config_flow",
    "documentation",
    "integration_type",
    "iot_class",
    "issue_tracker",
    "requirements",
    "version",
}


def _load(path: Path) -> dict[str, Any]:
    """Read a JSON document of the integration."""
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(value: Any, prefix: str = "") -> set[str]:
    """Flatten a nested mapping into dotted key paths."""
    if not isinstance(value, dict):
        return {prefix}
    keys: set[str] = set()
    for key, item in value.items():
        keys |= _keys(item, f"{prefix}.{key}" if prefix else key)
    return keys


def _placeholders(value: str) -> set[str]:
    """Return the {placeholders} of a translated string."""
    return set(re.findall(r"\{(\w+)\}", value))


def _flat(value: Any, prefix: str = "") -> dict[str, str]:
    """Flatten a nested mapping into dotted key paths with their strings."""
    if not isinstance(value, dict):
        return {prefix: str(value)}
    flat: dict[str, str] = {}
    for key, item in value.items():
        flat |= _flat(item, f"{prefix}.{key}" if prefix else key)
    return flat


def test_manifest_is_complete() -> None:
    """A custom integration needs all of these to load and to update."""
    manifest = _load(ROOT / "manifest.json")

    assert set(manifest) >= REQUIRED_MANIFEST_KEYS
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    # No third party dependency: the client is plain aiohttp, which ships with
    # Home Assistant.
    assert manifest["requirements"] == []
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["documentation"].startswith("https://")
    assert manifest["iot_class"] == "cloud_polling"


def test_english_translation_matches_strings() -> None:
    """translations/en.json is the shipped copy of strings.json."""
    assert _load(ROOT / "strings.json") == _load(ROOT / "translations" / "en.json")


@pytest.mark.parametrize("language", ["en", "es"])
def test_translation_has_every_key_and_placeholder(language: str) -> None:
    """A missing key is shown to the user as a raw translation key."""
    strings = _load(ROOT / "strings.json")
    translation = _load(ROOT / "translations" / f"{language}.json")

    assert _keys(translation) == _keys(strings)

    expected = _flat(strings)
    for key, value in _flat(translation).items():
        assert _placeholders(value) == _placeholders(expected[key]), key


def test_every_config_flow_step_is_translated() -> None:
    """Every step, error and abort reason the flow can produce has a string."""
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
    # The options flow lives in the same module but under its own strings key.
    config_source, _, options_source = source.partition("class RenfeOptionsFlow")
    strings = _load(ROOT / "strings.json")["config"]
    options = _load(ROOT / "strings.json")["options"]

    steps = set(re.findall(r'step_id="(\w+)"', config_source))
    # The menu step is declared by async_show_menu, which takes a step_id too.
    assert set(strings["step"]) == steps
    assert set(options["step"]) == set(re.findall(r'step_id="(\w+)"', options_source))

    menu_options = set(strings["step"]["user"]["menu_options"])
    assert menu_options == steps - {"user", "pick"}

    # Errors and abort reasons are string literals in the flow.
    assigned = set(re.findall(r'errors\[[^\]]+\] = "(\w+)"', source))
    assigned |= set(re.findall(r'reason="(\w+)"', source))
    known = set(strings["error"]) | set(strings["abort"])
    assert assigned <= known, assigned - known
    # already_configured comes from _abort_if_unique_id_configured.
    assert "already_configured" in strings["abort"]


def test_every_options_field_is_described() -> None:
    """The options form is unusable without a label for each field."""
    init = _load(ROOT / "strings.json")["options"]["step"]["init"]

    assert set(init["data"]) == set(init["data_description"])


def test_every_sensor_translation_key_exists() -> None:
    """A sensor with an unknown translation key renders without a name."""
    source = (ROOT / "sensor.py").read_text(encoding="utf-8")
    entity_strings = _load(ROOT / "strings.json")["entity"]["sensor"]

    used = set(re.findall(r'_attr_translation_key = "(\w+)"', source))
    assert used == set(entity_strings)


def test_services_yaml_matches_the_registered_action() -> None:
    """The action description in the UI comes from these two files agreeing."""
    services = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))
    strings = _load(ROOT / "strings.json")["services"]

    assert set(services) == {SERVICE_REFRESH}
    assert set(strings) == {SERVICE_REFRESH}
    assert services[SERVICE_REFRESH]["target"]["entity"]["integration"] == DOMAIN


def test_service_validation_error_is_translated() -> None:
    """A ServiceValidationError without a string shows its key to the user."""
    source = (ROOT / "services.py").read_text(encoding="utf-8")
    exceptions = _load(ROOT / "strings.json")["exceptions"]

    used = set(re.findall(r'translation_key="(\w+)"', source))
    assert used == set(exceptions)
