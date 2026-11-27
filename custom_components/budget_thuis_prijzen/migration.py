"""Overstap van het oude domein budgetthuis naar budget_thuis_prijzen.

Tot en met versie 0.4.0 heette het domein budgetthuis. Dat domein is ook van het
oorspronkelijke project (lvaoliveira/ha-budgetthuis-prices), dus een entry onder
budgetthuis hoeft niet van deze integratie te zijn. Daarom gebeurt er nooit iets
vanzelf: staat er zo'n entry, dan meldt deze integratie dat als reparatie onder
Instellingen > Systeem > Reparaties. Die melding toont welke entry, welk apparaat
en hoeveel entiteiten overgenomen worden, en pas na bevestiging (zie repairs.py)
gebeurt het volgende:

- de contractinstellingen (opties) gaan mee naar de nieuwe entry;
- elke entiteit verhuist in het entiteitenregister naar het nieuwe domein, met
  behoud van entity-ID, eigen naam, icoon en geschiedenis;
- het apparaat verhuist mee, met behoud van ruimte, labels en eigen naam;
- entiteiten en een apparaat die deze integratie al vers had aangemaakt, maken
  plaats voor de oude, zodat er geen dubbelen ontstaan;
- de oude entry wordt daarna verwijderd.

Dat kan alleen als de oude integratie niet meer draait. Draait ze nog, dan stopt
de bevestiging met een melding en blijft alles staan.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, OLD_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Toestanden van de nieuwe entry waarin de overstap veilig kan: geladen (dan
# wordt eerst ontladen) of zonder draaiende entiteiten.
_SAFE_STATES = {
    ConfigEntryState.LOADED,
    ConfigEntryState.NOT_LOADED,
    ConfigEntryState.SETUP_ERROR,
    ConfigEntryState.SETUP_RETRY,
    ConfigEntryState.MIGRATION_ERROR,
}

# Eén vaste melding: er is hooguit één entry van het oude domein.
ISSUE_ID = "migrate_old_domain"


@callback
def async_check_old_domain(hass: HomeAssistant) -> None:
    """Meld een entry van het oude domein als reparatie, of ruim de melding op.

    Er wordt hier niets overgenomen of gewijzigd; dat gebeurt pas als de
    gebruiker de reparatie bevestigt.
    """
    old = _async_old_entry(hass)
    if old is None:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
        return

    device = dr.async_get(hass).async_get_device(
        identifiers={(OLD_DOMAIN, old.entry_id)}
    )
    entities = er.async_entries_for_config_entry(er.async_get(hass), old.entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_ID,
        translation_placeholders={
            "old_domain": OLD_DOMAIN,
            "entry_title": old.title,
            "device_name": _device_name(device),
            "entity_count": str(len(entities)),
        },
    )


def _async_old_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """De entry van het oude domein, als die er is."""
    old_entries = hass.config_entries.async_entries(OLD_DOMAIN)
    return old_entries[0] if old_entries else None


def _device_name(device: dr.DeviceEntry | None) -> str:
    """De naam van het apparaat zoals de gebruiker hem ziet, of een streepje."""
    if device is None:
        return "-"
    return device.name_by_user or device.name or "-"


async def async_migrate_old_domain(
    hass: HomeAssistant, old: ConfigEntry, entry: ConfigEntry
) -> bool:
    """Neem de entry old van het oude domein over in entry.

    Alleen aanroepen na bevestiging door de gebruiker, met de oude integratie
    niet meer geladen. De nieuwe entry wordt hiervoor even ontladen, zodat er
    geen draaiende entiteiten in de weg zitten, en daarna weer opgezet.

    Geeft False terug als dat ontladen mislukt, of eerder al mislukte
    (FAILED_UNLOAD); er is dan niets gewijzigd, zodat de gebruiker het na een
    herstart veilig opnieuw kan proberen.
    """
    _LOGGER.info("Instellingen en entiteiten overnemen van %s", OLD_DOMAIN)

    if entry.state not in _SAFE_STATES:
        # Bijvoorbeeld FAILED_UNLOAD na een eerdere mislukte poging: er kunnen
        # nog entiteiten draaien, dus pas na een herstart verder.
        _LOGGER.warning("Overstap van %s afgebroken: %s", OLD_DOMAIN, entry.state)
        return False

    was_loaded = entry.state is ConfigEntryState.LOADED
    if was_loaded and not await hass.config_entries.async_unload(entry.entry_id):
        # Er kunnen nog entiteiten draaien; dan niets aan de registers veranderen.
        _LOGGER.warning("Ontladen mislukt, overstap van %s afgebroken", OLD_DOMAIN)
        return False

    # Alleen overnemen als de nieuwe entry nog geen eigen instellingen heeft.
    if not entry.options:
        hass.config_entries.async_update_entry(entry, options=dict(old.options))

    _async_move_entities(hass, old.entry_id, entry.entry_id)
    _async_move_device(hass, old.entry_id, entry.entry_id)

    # Entiteiten en apparaat hangen nu aan de nieuwe entry; de oude kan weg.
    await hass.config_entries.async_remove(old.entry_id)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)

    if was_loaded:
        await hass.config_entries.async_setup(entry.entry_id)
    return True


def _async_move_entities(hass: HomeAssistant, old_id: str, new_id: str) -> None:
    """Verhuis de entiteiten van de oude entry naar de nieuwe."""
    registry = er.async_get(hass)
    old_prefix = f"{old_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, old_id):
        new_unique_id = f"{new_id}_{reg_entry.unique_id.removeprefix(old_prefix)}"
        # Een vers aangemaakte entiteit van deze integratie maakt plaats.
        if fresh := registry.async_get_entity_id(
            reg_entry.domain, DOMAIN, new_unique_id
        ):
            registry.async_remove(fresh)
        # Na een herstart zet Home Assistant voor niet-geladen entiteiten een
        # "restored" toestand neer; die moet weg voordat ze mogen verhuizen.
        hass.states.async_remove(reg_entry.entity_id)
        registry.async_update_entity_platform(
            reg_entry.entity_id,
            DOMAIN,
            new_config_entry_id=new_id,
            new_unique_id=new_unique_id,
        )


def _async_move_device(hass: HomeAssistant, old_id: str, new_id: str) -> None:
    """Verhuis het apparaat, zodat ruimte, labels en eigen naam blijven staan."""
    registry = dr.async_get(hass)
    old_device = registry.async_get_device(identifiers={(OLD_DOMAIN, old_id)})
    if old_device is None:
        return
    # Een vers aangemaakt apparaat van deze integratie maakt plaats.
    if fresh := registry.async_get_device(identifiers={(DOMAIN, new_id)}):
        registry.async_remove_device(fresh.id)
    # Toevoegen en weghalen in twee stappen: in één aanroep raakt het register
    # de koppeling met de nieuwe entry kwijt.
    registry.async_update_device(
        old_device.id,
        new_identifiers={(DOMAIN, new_id)},
        add_config_entry_id=new_id,
    )
    registry.async_update_device(old_device.id, remove_config_entry_id=old_id)
