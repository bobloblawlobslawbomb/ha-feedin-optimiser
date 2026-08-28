"""Coordinator: gathers HA state, runs the optimiser, exposes the plan."""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from collections import defaultdict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAPACITY_KWH,
    CONF_CYCLE_COST,
    CONF_EXPORT_WINDOWS,
    CONF_HORIZON_HOURS,
    CONF_IMPORT_WINDOWS,
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
    SLOT_MINUTES,
    UPDATE_INTERVAL_MINUTES,
)
from .forecast import build_slots
from .optimiser import BatterySpec, optimise
from .tariff import DEFAULT_EXPORT_WINDOWS, DEFAULT_IMPORT_WINDOWS, parse_windows

_LOGGER = logging.getLogger(__name__)

PER_DAY = 24 * 60 // SLOT_MINUTES
LOAD_HISTORY_DAYS = 14


class FeedinOptimiserCoordinator(DataUpdateCoordinator):
    """Recomputes the dispatch plan on a fixed interval."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=dt.timedelta(minutes=UPDATE_INTERVAL_MINUTES),
            config_entry=entry,
        )
        self.entry = entry
        self._load_profile: list[float] | None = None
        self._load_profile_day: dt.date | None = None

    # -- config helpers -----------------------------------------------------
    def _opt(self, key, default):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    def _float_state(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", None, ""):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    # -- load profile -------------------------------------------------------
    async def _async_load_profile(self) -> list[float]:
        """Median house load per slot-of-day, learned from recorder history.

        Cached for the day; falls back to a flat estimate when history is
        unavailable (e.g. recorder purged or a fresh install).
        """
        today = dt_util.now().date()
        if self._load_profile is not None and self._load_profile_day == today:
            return self._load_profile

        entity_id = self._opt(CONF_LOAD_SENSOR, None)
        profile = [300.0] * PER_DAY

        if entity_id:
            try:
                from homeassistant.components.recorder import get_instance, history

                end = dt_util.utcnow()
                start = end - dt.timedelta(days=LOAD_HISTORY_DAYS)
                states = await get_instance(self.hass).async_add_executor_job(
                    lambda: history.state_changes_during_period(
                        self.hass, start, end, entity_id,
                        include_start_time_state=False, no_attributes=True,
                    )
                )
                buckets: dict[int, list[float]] = defaultdict(list)
                for st in states.get(entity_id, []):
                    try:
                        val = float(st.state)
                    except (TypeError, ValueError):
                        continue
                    if val < 0 or val > 50000:
                        continue
                    local = dt_util.as_local(st.last_changed)
                    idx = (local.hour * 60 + local.minute) // SLOT_MINUTES
                    buckets[idx].append(val)
                if buckets:
                    # Median resists the EV/battery charging spikes that would
                    # otherwise inflate the free-power window and get planned for
                    # twice — once as load, once as the charge this planner sets.
                    for i in range(PER_DAY):
                        if buckets.get(i):
                            profile[i] = statistics.median(buckets[i])
            except Exception as err:  # noqa: BLE001 - history is best-effort
                _LOGGER.warning("Load history unavailable, using flat profile: %s", err)

        self._load_profile = profile
        self._load_profile_day = today
        return profile

    # -- main update --------------------------------------------------------
    async def _async_update_data(self) -> dict:
        soc = self._float_state(self._opt(CONF_SOC_SENSOR, None))
        if soc is None:
            raise UpdateFailed("Battery SOC sensor is unavailable")

        capacity = float(self._opt(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        reserve = float(self._opt(CONF_RESERVE_SOC, DEFAULT_RESERVE_SOC))

        spec = BatterySpec(
            capacity_kwh=capacity,
            max_charge_kw=float(self._opt(CONF_MAX_CHARGE_KW, DEFAULT_MAX_CHARGE_KW)),
            max_discharge_kw=float(self._opt(CONF_MAX_DISCHARGE_KW,
                                             DEFAULT_MAX_DISCHARGE_KW)),
            max_export_kw=float(self._opt(CONF_MAX_EXPORT_KW, DEFAULT_MAX_EXPORT_KW)),
            reserve_soc=reserve,
            cycle_cost_per_kwh=float(self._opt(CONF_CYCLE_COST, DEFAULT_CYCLE_COST)),
        )

        imp = parse_windows(self._opt(CONF_IMPORT_WINDOWS, DEFAULT_IMPORT_WINDOWS))
        exp = parse_windows(self._opt(CONF_EXPORT_WINDOWS, DEFAULT_EXPORT_WINDOWS))

        pv_today = self._float_state(self._opt(CONF_PV_FORECAST_TODAY, None)) or 0.0
        pv_tomorrow = self._float_state(self._opt(CONF_PV_FORECAST_TOMORROW, None))
        if pv_tomorrow is None:
            pv_tomorrow = pv_today

        load_profile = await self._async_load_profile()

        now = dt_util.now()
        # Align to the current slot boundary so the plan lines up with the clock.
        aligned = now.replace(
            minute=(now.minute // SLOT_MINUTES) * SLOT_MINUTES,
            second=0, microsecond=0,
        )
        horizon = int(self._opt(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS))

        # Today's remaining generation is what matters; Solcast's "today" total
        # already covers the whole day, so scale it down by how much is left.
        slots = build_slots(
            start=aligned,
            horizon_hours=horizon,
            slot_minutes=SLOT_MINUTES,
            import_windows=imp,
            export_windows=exp,
            pv_daily_kwh=[pv_today, pv_tomorrow, pv_tomorrow],
            load_profile_w=load_profile,
        )

        try:
            plan = await self.hass.async_add_executor_job(
                lambda: optimise(slots, spec, soc_start=soc, steps=96)
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Optimisation failed: {err}") from err

        current = plan.slots[0] if plan.slots else None
        return {
            "plan": plan,
            "generated_at": now,
            "soc": soc,
            "current": current,
            "action": current.action if current else "idle",
            "saving": plan.saving,
            "total_cost": plan.total_cost,
            "baseline_cost": plan.baseline_cost,
            "horizon_hours": horizon,
        }
