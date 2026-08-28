"""Advisory sensors. This integration does not actuate anything."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FeedinOptimiserCoordinator

ACTION_LABELS = {
    "idle": "Idle",
    "charge_pv": "Charge from solar",
    "charge_grid": "Charge from grid",
    "discharge_house": "Discharge to house",
    "discharge_export": "Discharge to grid",
}

ACTION_ICONS = {
    "idle": "mdi:battery",
    "charge_pv": "mdi:solar-power",
    "charge_grid": "mdi:transmission-tower-import",
    "discharge_house": "mdi:home-lightning-bolt",
    "discharge_export": "mdi:transmission-tower-export",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeedinOptimiserCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        RecommendedActionSensor(coordinator, entry),
        PlanSavingSensor(coordinator, entry),
        NextChangeSensor(coordinator, entry),
        PlannedExportSensor(coordinator, entry),
    ])


class _Base(CoordinatorEntity[FeedinOptimiserCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Feed-in Optimiser",
            "manufacturer": "Hermes",
            "model": "Battery dispatch planner",
        }


class RecommendedActionSensor(_Base):
    _attr_name = "Recommended action"
    _attr_icon = "mdi:battery-sync"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_recommended_action"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if not data:
            return None
        return ACTION_LABELS.get(data["action"], data["action"])

    @property
    def icon(self) -> str:
        data = self.coordinator.data
        return ACTION_ICONS.get(data["action"], "mdi:battery") if data else "mdi:battery"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if not data:
            return {}
        cur = data.get("current")
        plan = data["plan"]
        # Compact schedule: only the points where the recommendation changes.
        schedule = []
        last = None
        base = data["generated_at"]
        for sp in plan.slots:
            if sp.action != last:
                moment = base + dt.timedelta(minutes=sp.slot.start_min)
                schedule.append({
                    "time": moment.isoformat(timespec="minutes"),
                    "action": ACTION_LABELS.get(sp.action, sp.action),
                    "soc": round(sp.soc_start, 1),
                    "import_price": round(sp.slot.import_price, 3),
                    "export_price": round(sp.slot.export_price, 3),
                })
                last = sp.action
        return {
            "raw_action": data["action"],
            "battery_kwh_this_slot": round(cur.battery_kwh, 3) if cur else 0.0,
            "grid_kwh_this_slot": round(cur.grid_kwh, 3) if cur else 0.0,
            "soc": round(data["soc"], 1),
            "horizon_hours": data["horizon_hours"],
            "generated_at": data["generated_at"].isoformat(timespec="seconds"),
            "advisory_only": True,
            "schedule": schedule,
        }


class PlanSavingSensor(_Base):
    _attr_name = "Estimated saving"
    _attr_native_unit_of_measurement = "AUD"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-plus"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_saving"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return round(data["saving"], 2) if data else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "optimised_cost": round(data["total_cost"], 2),
            "baseline_cost": round(data["baseline_cost"], 2),
            "note": "Versus having no battery, across the planning horizon.",
        }


class NextChangeSensor(_Base):
    _attr_name = "Next action change"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_next_change"

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data:
            return None
        plan = data["plan"]
        if not plan.slots:
            return None
        first = plan.slots[0].action
        for sp in plan.slots:
            if sp.action != first:
                return data["generated_at"] + dt.timedelta(minutes=sp.slot.start_min)
        return None


class PlannedExportSensor(_Base):
    _attr_name = "Planned export revenue"
    _attr_native_unit_of_measurement = "AUD"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_export_revenue"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        rev = -sum(sp.grid_kwh * sp.slot.export_price
                   for sp in data["plan"].slots if sp.grid_kwh < 0)
        return round(rev, 2)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if not data:
            return {}
        slots = data["plan"].slots
        exported = -sum(sp.grid_kwh for sp in slots if sp.grid_kwh < 0)
        from_batt = -sum(sp.grid_kwh for sp in slots
                         if sp.grid_kwh < 0 and sp.battery_kwh < 0)
        return {
            "exported_kwh": round(exported, 1),
            "exported_from_battery_kwh": round(from_batt, 1),
        }
