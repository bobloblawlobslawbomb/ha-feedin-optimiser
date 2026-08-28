"""Tariff modelling: maps wall-clock time to import/export prices.

Encodes a day-parted tariff as a list of (start_hhmm, end_hhmm, price) windows so
the whole thing stays declarative and user-editable from the config flow.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Window:
    start: int   # minutes past local midnight
    end: int     # exclusive; may wrap past midnight
    price: float
    label: str = ""

    def contains(self, minute: int) -> bool:
        if self.start <= self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end   # wraps midnight


def hhmm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def parse_windows(raw: Iterable[dict]) -> list[Window]:
    out = []
    for w in raw:
        out.append(Window(
            start=hhmm(w["start"]) if isinstance(w["start"], str) else int(w["start"]),
            end=hhmm(w["end"]) if isinstance(w["end"], str) else int(w["end"]),
            price=float(w["price"]),
            label=w.get("label", ""),
        ))
    return out


def price_at(windows: Sequence[Window], moment: dt.datetime, default: float = 0.0) -> float:
    minute = moment.hour * 60 + moment.minute
    for w in windows:
        if w.contains(minute):
            return w.price
    return default


def label_at(windows: Sequence[Window], moment: dt.datetime, default: str = "") -> str:
    minute = moment.hour * 60 + moment.minute
    for w in windows:
        if w.contains(minute):
            return w.label or default
    return default


# The user's Globird plan, derived from their existing HA automations.
DEFAULT_IMPORT_WINDOWS = [
    {"start": "11:00", "end": "14:00", "price": 0.00, "label": "Free"},
    {"start": "14:00", "end": "16:00", "price": 0.44, "label": "Shoulder"},
    {"start": "16:00", "end": "23:00", "price": 0.55, "label": "Peak"},
    {"start": "23:00", "end": "11:00", "price": 0.44, "label": "Shoulder"},
]

DEFAULT_EXPORT_WINDOWS = [
    {"start": "16:00", "end": "18:00", "price": 0.02, "label": "Regular"},
    {"start": "18:00", "end": "21:00", "price": 0.10, "label": "Super"},
    {"start": "21:00", "end": "23:00", "price": 0.02, "label": "Regular"},
    {"start": "23:00", "end": "16:00", "price": 0.00, "label": "Zero"},
]
