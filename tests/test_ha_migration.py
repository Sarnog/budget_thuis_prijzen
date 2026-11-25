"""Home Assistant-tests voor de overstap van het oude domein budgetthuis."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.budget_thuis_prijzen.const import (
    DOMAIN,
    OLD_DOMAIN,
    TARIFF_URL,
)
from tests.ha_helpers import DAY_1, DAY_2, NO_SURCHARGES, local, page, state_price

OLD_ENTITY_ID = "sensor.budget_thuis_dynamic_prices_current_price"
OLD_OPTIONS = {**NO_SURCHARGES, "inkoopvergoeding": 0.02}


async def _old_install(hass, state=ConfigEntryState.NOT_LOADED) -> MockConfigEntry:
    """Een entry plus geregistreerde entiteit zoals versie 0.4.0 ze achterliet."""
    old = MockConfigEntry(
        domain=OLD_DOMAIN, data={}, options=OLD_OPTIONS, unique_id=OLD_DOMAIN
    )
    old.add_to_hass(hass)
    old.mock_state(hass, state)
    er.async_get(hass).async_get_or_create(
        "sensor",
        OLD_DOMAIN,
        f"{old.entry_id}_current_price",
        config_entry=old,
        suggested_object_id="budget_thuis_dynamic_prices_current_price",
        original_icon="mdi:old",
    )
    # Na een herstart zet Home Assistant zo'n "restored" toestand neer.
    hass.states.async_set(OLD_ENTITY_ID, "unavailable", {"restored": True})
    return old


async def _setup_new(hass, aioclient_mock, freezer) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(local(DAY_1, 14, 23))
    aioclient_mock.get(TARIFF_URL, text=page([DAY_1, DAY_2]))
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_old_install_is_taken_over(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Instellingen en entity-ID's gaan mee; de oude entry verdwijnt."""
    old = await _old_install(hass)

    entry = await _setup_new(hass, aioclient_mock, freezer)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.options == OLD_OPTIONS
    assert hass.config_entries.async_get_entry(old.entry_id) is None

    reg_entry = er.async_get(hass).async_get(OLD_ENTITY_ID)
    assert reg_entry is not None
    assert reg_entry.platform == DOMAIN
    assert reg_entry.config_entry_id == entry.entry_id
    assert reg_entry.unique_id == f"{entry.entry_id}_current_price"
    # (0,14 + 0,1) zonder BTW, plus de overgenomen inkoopvergoeding van 0,02
    assert state_price(hass, OLD_ENTITY_ID) == 0.26
    # Er komt geen tweede, nieuwe huidige-prijssensor bij.
    assert hass.states.get("sensor.budget_thuis_prijzen_current_price") is None


async def test_own_options_are_kept(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Heeft de nieuwe entry al instellingen, dan blijven die staan."""
    await _old_install(hass)
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(local(DAY_1, 14, 23))
    aioclient_mock.get(TARIFF_URL, text=page([DAY_1, DAY_2]))
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options=NO_SURCHARGES, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.options == NO_SURCHARGES
    assert state_price(hass, OLD_ENTITY_ID) == 0.24


async def test_waits_while_old_integration_runs(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Draait de oude integratie nog, dan wacht de setup en blijft alles staan."""
    old = await _old_install(hass, ConfigEntryState.LOADED)

    entry = await _setup_new(hass, aioclient_mock, freezer)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.async_get_entry(old.entry_id) is old
    assert er.async_get(hass).async_get(OLD_ENTITY_ID).platform == OLD_DOMAIN
