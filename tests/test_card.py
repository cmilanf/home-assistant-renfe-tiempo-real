"""Tests for the registration of the Lovelace card."""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.brands.const import ALLOWED_IMAGES
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.core import HomeAssistant

import custom_components.renfe_tiempo_real as renfe
from custom_components.renfe_tiempo_real.card import async_register_frontend
from custom_components.renfe_tiempo_real.const import CARD_FILENAME, FRONTEND_URL_BASE

CARD_URL = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v=0.2.0"


def _storage_resources(hass: HomeAssistant, items: list[dict]) -> MagicMock:
    """Install a minimal storage-mode Lovelace resource collection."""
    resources = MagicMock()
    resources.async_get_info = AsyncMock(return_value={"resources": len(items)})
    resources.async_items.return_value = items
    resources.async_create_item = AsyncMock()
    resources.async_update_item = AsyncMock()
    hass.data[LOVELACE_DATA] = SimpleNamespace(
        resource_mode=MODE_STORAGE, resources=resources
    )
    return resources


async def test_card_is_served_and_persisted_as_lovelace_resource(
    hass: HomeAssistant,
) -> None:
    """Storage mode persists the module instead of relying on a late JS event."""
    hass.config.components.add("frontend")
    hass.http = AsyncMock()
    resources = _storage_resources(hass, [])

    with patch("custom_components.renfe_tiempo_real.card.add_extra_js_url") as add_js:
        await async_register_frontend(hass)

    static_paths = hass.http.async_register_static_paths.await_args[0][0]
    assert len(static_paths) == 1
    assert static_paths[0].url_path == FRONTEND_URL_BASE
    assert static_paths[0].path.endswith(
        "/custom_components/renfe_tiempo_real/frontend"
    )
    assert static_paths[0].cache_headers is False

    resources.async_get_info.assert_awaited_once_with()
    resources.async_create_item.assert_awaited_once_with(
        {"res_type": "module", "url": CARD_URL}
    )
    resources.async_update_item.assert_not_awaited()
    add_js.assert_not_called()


async def test_existing_card_resource_is_not_duplicated(hass: HomeAssistant) -> None:
    """Repeated setup leaves an up-to-date card resource untouched."""
    hass.config.components.add("frontend")
    hass.http = AsyncMock()
    resources = _storage_resources(
        hass, [{"id": "card-resource", "type": "module", "url": CARD_URL}]
    )

    await async_register_frontend(hass)

    resources.async_create_item.assert_not_awaited()
    resources.async_update_item.assert_not_awaited()


async def test_existing_card_resource_is_updated(hass: HomeAssistant) -> None:
    """An upgrade updates the cache-busting URL and preserves one resource."""
    hass.config.components.add("frontend")
    hass.http = AsyncMock()
    old_url = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v=0.1.0"
    resources = _storage_resources(
        hass, [{"id": "card-resource", "type": "js", "url": old_url}]
    )

    await async_register_frontend(hass)

    resources.async_update_item.assert_awaited_once_with(
        "card-resource", {"url": CARD_URL, "res_type": "module"}
    )
    resources.async_create_item.assert_not_awaited()


async def test_card_uses_extra_module_without_storage_resources(
    hass: HomeAssistant,
) -> None:
    """YAML/headless Lovelace setups retain the compatible frontend fallback."""
    hass.config.components.add("frontend")
    hass.http = AsyncMock()

    with patch("custom_components.renfe_tiempo_real.card.add_extra_js_url") as add_js:
        await async_register_frontend(hass)

    add_js.assert_called_once_with(hass, CARD_URL)


async def test_card_registration_skipped_without_frontend(
    hass: HomeAssistant,
) -> None:
    """A headless install must not fail because the card cannot be served."""
    assert "frontend" not in hass.config.components
    hass.http = AsyncMock()

    with patch("custom_components.renfe_tiempo_real.card.add_extra_js_url") as add_js:
        await async_register_frontend(hass)

    hass.http.async_register_static_paths.assert_not_awaited()
    add_js.assert_not_called()


async def test_brand_images_are_shipped() -> None:
    """The brand images let Devices & services show the integration logo.

    Home Assistant's ``brands`` component serves images from a ``brand``
    directory inside a custom integration, which is only consulted when the
    directory exists (``Integration.has_branding``). The names and the fallback
    chain come from ``homeassistant.components.brands.const``.
    """
    brand = Path(renfe.__file__).parent / "brand"
    assert brand.is_dir(), "the directory name is what enables local branding"

    shipped = {path.name for path in brand.iterdir() if path.is_file()}
    assert shipped <= ALLOWED_IMAGES, f"unexpected files: {shipped - ALLOWED_IMAGES}"
    # icon.png is the root of every fallback chain, so it must be present.
    assert "icon.png" in shipped
    assert {"icon@2x.png", "logo.png", "logo@2x.png"} <= shipped

    for name in shipped:
        data = (brand / name).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"{name} is not a PNG"

    def size(name: str) -> tuple[int, int]:
        header = (brand / name).read_bytes()[16:24]
        return struct.unpack(">II", header)

    # Square icons, natural aspect logos, per the brands conventions.
    assert size("icon.png") == (256, 256)
    assert size("icon@2x.png") == (512, 512)
    assert size("logo.png")[1] == 256
    assert size("logo@2x.png")[1] == 512


async def test_card_file_exists_where_it_is_served_from() -> None:
    """The file the registration points at is actually shipped."""
    frontend = Path(renfe.__file__).parent / "frontend"
    card = frontend / CARD_FILENAME
    assert card.is_file()
    source = card.read_text(encoding="utf-8")
    assert 'customElements.define("renfe-tiempo-real-card"' in source
    assert 'callService("renfe_tiempo_real", "refresh"' in source

    # Only the card is served; the artwork lives outside the integration.
    assert {path.name for path in frontend.iterdir()} == {CARD_FILENAME}


async def test_card_version_matches_the_manifest() -> None:
    """The card banner and the cache-busting URL must not drift apart."""
    import json

    root = Path(renfe.__file__).parent
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    card = (root / "frontend" / CARD_FILENAME).read_text(encoding="utf-8")

    assert f'const CARD_VERSION = "{manifest["version"]}"' in card
