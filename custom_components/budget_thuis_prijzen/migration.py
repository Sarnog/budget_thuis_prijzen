"""Overstap van het oude domein budgetthuis naar budget_thuis_prijzen.

Tot en met versie 0.4.0 heette het domein budgetthuis. Staat er bij het opzetten
nog een config entry van dat oude domein, dan neemt deze integratie die over:

- de contractinstellingen (opties) gaan mee naar de nieuwe entry;
- elke entiteit verhuist in het entiteitenregister naar het nieuwe domein, met
  behoud van entity-ID, eigen naam, icoon en geschiedenis;
- de oude entry wordt daarna verwijderd.

Dat kan alleen als de oude integratie niet meer draait. Draait ze nog, dan wacht
de setup (ConfigEntryNotReady) tot de oude map weg is en Home Assistant herstart.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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

    registry = er.async_get(hass)
    old_prefix = f"{old.entry_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, old.entry_id):
        # Na een herstart zet Home Assistant voor niet-geladen entiteiten een
        # "restored" toestand neer; die moet weg voordat ze mogen verhuizen.
        hass.states.async_remove(reg_entry.entity_id)
        key = reg_entry.unique_id.removeprefix(old_prefix)
        registry.async_update_entity_platform(
            reg_entry.entity_id,
            DOMAIN,
            new_config_entry_id=entry.entry_id,
            new_unique_id=f"{entry.entry_id}_{key}",
            new_device_id=None,  # het nieuwe apparaat koppelt zich bij het laden
        )

    # De entiteiten hangen nu aan de nieuwe entry; de oude kan weg.
    await hass.config_entries.async_remove(old.entry_id)
