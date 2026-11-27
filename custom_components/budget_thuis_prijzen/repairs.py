"""Reparaties voor Budget Thuis Prijzen.

De enige reparatie is de overstap van het oude domein budgetthuis (zie
migration.py, ISSUE_ID). De gebruiker ziet eerst welke entry, welk apparaat en hoeveel
entiteiten overgenomen worden, en pas na bevestiging gebeurt er iets.
"""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, OLD_DOMAIN
from .migration import async_migrate_old_domain


class MigrateOldDomainFlow(RepairsFlow):
    """Vraag eerst bevestiging en neem dan de oude budgetthuis-entry over."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Begin bij de bevestigingsstap."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Toon wat er overgenomen wordt en voer het pas uit na bevestiging."""
        if user_input is None:
            # De beschrijving toont de gegevens die bij de melding horen.
            issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
            placeholders = issue.translation_placeholders if issue else None
            return self.async_show_form(
                step_id="confirm", description_placeholders=placeholders
            )

        old_entries = self.hass.config_entries.async_entries(OLD_DOMAIN)
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not old_entries or not entries:
            # De oude of de nieuwe entry is inmiddels verwijderd.
            return self.async_abort(reason="nothing_to_migrate")
        old = old_entries[0]
        if old.state is ConfigEntryState.LOADED:
            # Draaiende entiteiten kunnen niet verhuizen; er verandert niets.
            return self.async_abort(
                reason="old_integration_running",
                description_placeholders={"old_domain": OLD_DOMAIN},
            )

        if not await async_migrate_old_domain(self.hass, old, entries[0]):
            # Ontladen van de nieuwe entry mislukte; er is niets gewijzigd.
            return self.async_abort(reason="unload_failed")
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Maak de flow voor een reparatie van deze integratie.

    Er is maar één soort reparatie (ISSUE_ID), dus issue_id kiest niets.
    """
    return MigrateOldDomainFlow()
