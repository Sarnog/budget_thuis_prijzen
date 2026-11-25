"""Overstap van het oude domein budgetthuis naar budget_thuis_prijzen.

Tot en met versie 0.4.0 heette het domein budgetthuis. Staat er bij het opzetten
nog een config entry van dat oude domein, dan neemt deze integratie die over:

- de contractinstellingen (opties) gaan mee naar de nieuwe entry;
- elke entiteit verhuist in het entiteitenregister naar het nieuwe domein, met
  behoud van entity-ID, eigen naam, icoon en geschiedenis;
- het apparaat verhuist mee, met behoud van ruimte, labels en eigen naam;
- entiteiten en een apparaat die een eerdere 0.5.0-installatie al vers had
  aangemaakt, maken plaats voor de oude, zodat er geen dubbelen ontstaan;
- de oude entry wordt daarna verwijderd.

Dat kan alleen als de oude integratie niet meer draait. Draait ze nog, dan wacht
de setup (ConfigEntryNotReady) tot de oude map weg is en Home Assistant herstart.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, OLD_DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_migrate_old_domain(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Neem een eventuele entry van het oude domein over in entry."""
    old_entries = hass.config_entries.async_entries(OLD_DOMAIN)
    if not old_entries:
        return
    old = old_entries[0]

    if old.state is ConfigEntryState.LOADED:
        # Draaiende entiteiten kunnen niet verhuizen. Pas na het verwijderen van
        # custom_components/budgetthuis en een herstart lukt de overstap.
        raise ConfigEntryNotReady(
            "De oude integratie budgetthuis draait nog. Verwijder de map "
            "custom_components/budgetthuis en herstart Home Assistant."
        )

    _LOGGER.info("Instellingen en entiteiten overnemen van %s", OLD_DOMAIN)

    # Alleen overnemen als de nieuwe entry nog geen eigen instellingen heeft.
    if not entry.options:
        hass.config_entries.async_update_entry(entry, options=dict(old.options))

    _async_move_entities(hass, old.entry_id, entry.entry_id)
    _async_move_device(hass, old.entry_id, entry.entry_id)

    # Entiteiten en apparaat hangen nu aan de nieuwe entry; de oude kan weg.
    await hass.config_entries.async_remove(old.entry_id)


def _async_move_entities(hass: HomeAssistant, old_id: str, new_id: str) -> None:
    """Verhuis de entiteiten van de oude entry naar de nieuwe."""
    registry = er.async_get(hass)
    old_prefix = f"{old_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, old_id):
        new_unique_id = f"{new_id}_{reg_entry.unique_id.removeprefix(old_prefix)}"
        # Een verse entiteit van een eerdere 0.5.0-installatie maakt plaats.
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
    # Een vers apparaat van een eerdere 0.5.0-installatie maakt plaats.
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
