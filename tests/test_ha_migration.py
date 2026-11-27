"""Home Assistant-tests voor de overstap van het oude domein budgetthuis.

De overstap gebeurt nooit vanzelf: er komt een reparatiemelding, en pas na
bevestiging in de fix flow wordt de oude entry overgenomen.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.budget_thuis_prijzen.const import (
    DOMAIN,
    OLD_DOMAIN,
    TARIFF_URL,
)
from custom_components.budget_thuis_prijzen.migration import (
    ISSUE_ID,
    async_migrate_old_domain,
)
from custom_components.budget_thuis_prijzen.repairs import async_create_fix_flow
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
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=old.entry_id,
        identifiers={(OLD_DOMAIN, old.entry_id)},
        name="Budget Thuis Dynamic Prices",
    )
    dr.async_get(hass).async_update_device(device.id, area_id="meterkast")
    er.async_get(hass).async_get_or_create(
        "sensor",
        OLD_DOMAIN,
        f"{old.entry_id}_current_price",
        config_entry=old,
        device_id=device.id,
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


async def _confirm(hass, entry: MockConfigEntry | None = None) -> dict:
    """Doorloop de fix flow zoals de gebruiker dat doet: tonen, dan bevestigen."""
    flow = await async_create_fix_flow(hass, ISSUE_ID, None)
    flow.hass = hass
    flow.issue_id = ISSUE_ID
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["step_id"] == "confirm"
    result = await flow.async_step_confirm({})
    await hass.async_block_till_done()
    return result


async def test_nothing_happens_without_confirmation(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Alleen een melding; de oude entry, entiteit en apparaat blijven staan."""
    old = await _old_install(hass)

    entry = await _setup_new(hass, aioclient_mock, freezer)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.options == {}
    assert hass.config_entries.async_get_entry(old.entry_id) is old
    reg_entry = er.async_get(hass).async_get(OLD_ENTITY_ID)
    assert reg_entry.platform == OLD_DOMAIN
    assert reg_entry.config_entry_id == old.entry_id
    assert dr.async_get(hass).async_get_device(identifiers={(OLD_DOMAIN, old.entry_id)})

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "old_domain": OLD_DOMAIN,
        "entry_title": old.title,
        "device_name": "Budget Thuis Dynamic Prices",
        "entity_count": "1",
    }


async def test_no_issue_without_old_install(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Zonder oude entry geen melding, en een oude melding verdwijnt."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_ID,
    )

    entry = await _setup_new(hass, aioclient_mock, freezer)

    assert entry.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_old_install_is_taken_over(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Na bevestiging gaan instellingen en entity-ID's mee; de oude entry verdwijnt."""
    old = await _old_install(hass)
    entry = await _setup_new(hass, aioclient_mock, freezer)

    result = await _confirm(hass)

    assert result["type"] == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None

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

    # Het apparaat verhuist mee, met zijn ruimte.
    device = dr.async_get(hass).async_get(reg_entry.device_id)
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.config_entries == {entry.entry_id}
    assert device.area_id == "meterkast"


async def test_fresh_050_install_makes_way(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Wat een eerdere 0.5.0-installatie vers aanmaakte, maakt plaats voor het oude."""
    old = await _old_install(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    fresh_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_current_price",
        config_entry=entry,
        device_id=fresh_device.id,
        suggested_object_id="budget_thuis_prijzen_current_price",
    )

    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(local(DAY_1, 14, 23))
    aioclient_mock.get(TARIFF_URL, text=page([DAY_1, DAY_2]))
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await _confirm(hass)

    entities = er.async_get(hass)
    assert entities.async_get("sensor.budget_thuis_prijzen_current_price") is None
    assert entities.async_get(OLD_ENTITY_ID).unique_id == (
        f"{entry.entry_id}_current_price"
    )
    assert dr.async_get(hass).async_get(fresh_device.id) is None
    assert hass.config_entries.async_get_entry(old.entry_id) is None


async def test_own_options_are_kept(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Heeft de nieuwe entry al instellingen, dan blijven die staan."""
    old = await _old_install(hass)
    # Ook zonder oud apparaat (bijv. zelf verwijderd) lukt de overstap.
    er.async_get(hass).async_update_entity(OLD_ENTITY_ID, device_id=None)
    devices = dr.async_get(hass)
    devices.async_remove_device(
        devices.async_get_device(identifiers={(OLD_DOMAIN, old.entry_id)}).id
    )
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(local(DAY_1, 14, 23))
    aioclient_mock.get(TARIFF_URL, text=page([DAY_1, DAY_2]))
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options=NO_SURCHARGES, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await _confirm(hass)

    assert entry.options == NO_SURCHARGES
    assert state_price(hass, OLD_ENTITY_ID) == 0.24


async def test_refuses_while_old_integration_runs(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Draait de oude integratie nog, dan stopt de bevestiging en blijft alles staan."""
    old = await _old_install(hass, ConfigEntryState.LOADED)
    entry = await _setup_new(hass, aioclient_mock, freezer)
    assert entry.state is ConfigEntryState.LOADED

    result = await _confirm(hass)

    assert result["type"] == "abort"
    assert result["reason"] == "old_integration_running"
    assert hass.config_entries.async_get_entry(old.entry_id) is old
    assert er.async_get(hass).async_get(OLD_ENTITY_ID).platform == OLD_DOMAIN
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_aborts_when_old_entry_is_gone(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Is de oude entry inmiddels weg, dan valt er niets over te nemen."""
    old = await _old_install(hass)
    entry = await _setup_new(hass, aioclient_mock, freezer)
    await hass.config_entries.async_remove(old.entry_id)

    result = await _confirm(hass)

    assert result["type"] == "abort"
    assert result["reason"] == "nothing_to_migrate"
    assert entry.state is ConfigEntryState.LOADED


async def test_removing_entry_clears_issue(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Wie de integratie verwijdert, houdt geen overstapmelding over."""
    await _old_install(hass)
    entry = await _setup_new(hass, aioclient_mock, freezer)

    await hass.config_entries.async_remove(entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_migrate_with_unloaded_new_entry(
    hass, enable_custom_integrations
) -> None:
    """Een nog niet opgezette nieuwe entry blijft na de overstap gewoon uit."""
    old = await _old_install(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    await async_migrate_old_domain(hass, old, entry)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.options == OLD_OPTIONS
    assert er.async_get(hass).async_get(OLD_ENTITY_ID).platform == DOMAIN
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device.area_id == "meterkast"
    assert hass.config_entries.async_get_entry(old.entry_id) is None


async def test_aborts_when_unload_fails(
    hass, enable_custom_integrations, aioclient_mock, freezer
) -> None:
    """Lukt het ontladen niet, dan verandert er niets en kan het opnieuw."""
    old = await _old_install(hass)
    entry = await _setup_new(hass, aioclient_mock, freezer)

    with patch.object(
        hass.config_entries, "async_unload", AsyncMock(return_value=False)
    ):
        result = await _confirm(hass)

    assert result["type"] == "abort"
    assert result["reason"] == "unload_failed"
    assert entry.options == {}
    assert hass.config_entries.async_get_entry(old.entry_id) is old
    assert er.async_get(hass).async_get(OLD_ENTITY_ID).platform == OLD_DOMAIN
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_aborts_after_failed_unload(hass, enable_custom_integrations) -> None:
    """Na een eerder mislukte ontlading (FAILED_UNLOAD) verandert er niets."""
    old = await _old_install(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.FAILED_UNLOAD)

    assert not await async_migrate_old_domain(hass, old, entry)

    assert entry.options == {}
    assert hass.config_entries.async_get_entry(old.entry_id) is old
    assert er.async_get(hass).async_get(OLD_ENTITY_ID).platform == OLD_DOMAIN
