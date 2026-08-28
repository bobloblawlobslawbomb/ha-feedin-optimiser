"""Config flow for the Feed-in Optimiser."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAPACITY_KWH,
    CONF_CYCLE_COST,
    CONF_HORIZON_HOURS,
    CONF_LOAD_SENSOR,
    CONF_MAX_CHARGE_KW,
    CONF_MAX_DISCHARGE_KW,
    CONF_MAX_EXPORT_KW,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_RESERVE_SOC,
    CONF_SOC_SENSOR,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CYCLE_COST,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_CHARGE_KW,
    DEFAULT_MAX_DISCHARGE_KW,
    DEFAULT_MAX_EXPORT_KW,
    DEFAULT_RESERVE_SOC,
    DOMAIN,
)


def _sensor(device_class: str | None = None):
    cfg = {"domain": "sensor"}
    if device_class:
        cfg["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**cfg))


def _num(minimum, maximum, step, unit):
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step,
        unit_of_measurement=unit,
        mode=selector.NumberSelectorMode.BOX,
    ))


def schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the form. Every field carries a default so the flow renders."""
    return vol.Schema({
        vol.Required(CONF_SOC_SENSOR,
                     default=defaults.get(CONF_SOC_SENSOR, "")): _sensor("battery"),
        vol.Required(CONF_CAPACITY_KWH,
                     default=defaults.get(CONF_CAPACITY_KWH,
                                          DEFAULT_CAPACITY_KWH)): _num(1, 500, 0.01, "kWh"),
        vol.Optional(CONF_LOAD_SENSOR,
                     default=defaults.get(CONF_LOAD_SENSOR, "")): _sensor("power"),
        vol.Optional(CONF_PV_FORECAST_TODAY,
                     default=defaults.get(CONF_PV_FORECAST_TODAY, "")): _sensor(),
        vol.Optional(CONF_PV_FORECAST_TOMORROW,
                     default=defaults.get(CONF_PV_FORECAST_TOMORROW, "")): _sensor(),
        vol.Required(CONF_RESERVE_SOC,
                     default=defaults.get(CONF_RESERVE_SOC,
                                          DEFAULT_RESERVE_SOC)): _num(0, 90, 1, "%"),
        vol.Required(CONF_MAX_CHARGE_KW,
                     default=defaults.get(CONF_MAX_CHARGE_KW,
                                          DEFAULT_MAX_CHARGE_KW)): _num(0.5, 50, 0.1, "kW"),
        vol.Required(CONF_MAX_DISCHARGE_KW,
                     default=defaults.get(CONF_MAX_DISCHARGE_KW,
                                          DEFAULT_MAX_DISCHARGE_KW)): _num(0.5, 50, 0.1, "kW"),
        vol.Required(CONF_MAX_EXPORT_KW,
                     default=defaults.get(CONF_MAX_EXPORT_KW,
                                          DEFAULT_MAX_EXPORT_KW)): _num(0.5, 50, 0.1, "kW"),
        vol.Required(CONF_HORIZON_HOURS,
                     default=defaults.get(CONF_HORIZON_HOURS,
                                          DEFAULT_HORIZON_HOURS)): _num(12, 72, 1, "h"),
        vol.Required(CONF_CYCLE_COST,
                     default=defaults.get(CONF_CYCLE_COST,
                                          DEFAULT_CYCLE_COST)): _num(0, 1, 0.001, "AUD/kWh"),
    })


class FeedinOptimiserConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        # Singleton: one battery system per install.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned = {k: v for k, v in user_input.items() if v not in ("", None)}
            if cleaned.get(CONF_RESERVE_SOC, 0) >= 100:
                errors[CONF_RESERVE_SOC] = "reserve_too_high"
            if not errors:
                return self.async_create_entry(title="Feed-in Optimiser", data=cleaned)

        return self.async_show_form(
            step_id="user", data_schema=schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return FeedinOptimiserOptionsFlow(entry)


class FeedinOptimiserOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned = {k: v for k, v in user_input.items() if v not in ("", None)}
            if cleaned.get(CONF_RESERVE_SOC, 0) >= 100:
                errors[CONF_RESERVE_SOC] = "reserve_too_high"
            if not errors:
                return self.async_create_entry(title="", data=cleaned)

        merged = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init", data_schema=schema(user_input or merged), errors=errors
        )
