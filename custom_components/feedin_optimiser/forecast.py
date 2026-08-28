"""Builds optimiser inputs from live Home Assistant state.

Kept free of Home Assistant imports below the `build_slots` boundary so the
forecast maths can be unit-tested without a running HA instance.
"""

from __future__ import annotations

import datetime as dt
from typing import Sequence

from .optimiser import Slot
from .tariff import Window, price_at


def solar_shape(slot_count: int, slot_minutes: int, sunrise_h: float = 6.5,
                sunset_h: float = 17.5) -> list[float]:
    """Normalised clear-sky generation shape, one entry per slot of a day.

    A smooth parabola between sunrise and sunset. Crude versus a real irradiance
    model, but it only distributes a Solcast daily total across the day, and the
    daily total is the part that carries the forecasting skill.
    """
    per_day = 24 * 60 // slot_minutes
    out = []
    for i in range(slot_count):
        h = (i % per_day) * slot_minutes / 60.0
        if h <= sunrise_h or h >= sunset_h:
            out.append(0.0)
            continue
        mid = (sunrise_h + sunset_h) / 2.0
        half = (sunset_h - sunrise_h) / 2.0
        x = (h - mid) / half
        out.append(max(0.0, 1.0 - x * x))
    return out


def distribute_daily_pv(daily_kwh_by_day: Sequence[float], slot_count: int,
                        slot_minutes: int, start_slot_of_day: int) -> list[float]:
    """Spread each day's forecast total across that day's slots."""
    per_day = 24 * 60 // slot_minutes
    shape = solar_shape(per_day * (len(daily_kwh_by_day) + 1), slot_minutes)
    out = []
    for i in range(slot_count):
        abs_slot = start_slot_of_day + i
        day = abs_slot // per_day
        idx = abs_slot % per_day
        total = (daily_kwh_by_day[day] if day < len(daily_kwh_by_day)
                 else (daily_kwh_by_day[-1] if daily_kwh_by_day else 0.0))
        day_shape = shape[day * per_day:(day + 1) * per_day]
        denom = sum(day_shape) * (slot_minutes / 60.0)
        out.append(total * day_shape[idx] * (slot_minutes / 60.0) / denom
                   if denom > 0 else 0.0)
    return out


def build_slots(
    start: dt.datetime,
    horizon_hours: int,
    slot_minutes: int,
    import_windows: Sequence[Window],
    export_windows: Sequence[Window],
    pv_daily_kwh: Sequence[float],
    load_profile_w: Sequence[float],
) -> list[Slot]:
    """Assemble the planning horizon.

    ``load_profile_w`` is a per-slot-of-day average house load in watts, i.e.
    ``24*60/slot_minutes`` entries, indexed from local midnight.
    """
    per_day = 24 * 60 // slot_minutes
    count = horizon_hours * 60 // slot_minutes
    start_slot_of_day = (start.hour * 60 + start.minute) // slot_minutes
    dur_h = slot_minutes / 60.0

    pv = distribute_daily_pv(pv_daily_kwh, count, slot_minutes, start_slot_of_day)

    slots: list[Slot] = []
    for i in range(count):
        moment = start + dt.timedelta(minutes=i * slot_minutes)
        idx = (start_slot_of_day + i) % per_day
        watts = load_profile_w[idx] if idx < len(load_profile_w) else 300.0
        slots.append(Slot(
            start_min=i * slot_minutes,
            duration_h=dur_h,
            import_price=price_at(import_windows, moment),
            export_price=price_at(export_windows, moment),
            pv_kwh=pv[i],
            load_kwh=watts * dur_h / 1000.0,
        ))
    return slots
