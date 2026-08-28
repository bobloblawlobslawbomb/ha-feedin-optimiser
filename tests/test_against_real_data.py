"""Validate the optimiser against the user's real load profile and tariffs."""

import datetime as dt
import importlib.util
import json
import sys

BASE = "/home/hermes/ha-feedin-optimiser/custom_components/feedin_optimiser/"


def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE + name + ".py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


opt = load("optimiser")
tar = load("tariff")

imp_w = tar.parse_windows(tar.DEFAULT_IMPORT_WINDOWS)
exp_w = tar.parse_windows(tar.DEFAULT_EXPORT_WINDOWS)

# Real measured half-hourly median house load (W) pulled from HA history.
load_w = json.load(open("/tmp/load_profile.json"))

# The 11:00-14:00 readings are inflated by EV + battery charging that this
# planner itself controls. Substitute the surrounding baseline so we plan on
# genuine house demand and don't double-count.
baseline_day = sorted(v for i, v in enumerate(load_w) if v and not (22 <= i < 28))
BASE_FREE = 1500.0
adj = []
for i, v in enumerate(load_w):
    adj.append(BASE_FREE if 22 <= i < 28 else (v or 300.0))

spec = opt.BatterySpec(
    capacity_kwh=47.88,
    max_charge_kw=10.0,
    max_discharge_kw=10.0,
    reserve_soc=20.0,
    max_export_kw=10.0,
)


def build_day(pv_total_kwh: float, start_hour: int = 0):
    """48 half-hour slots starting at local midnight."""
    slots = []
    # crude clear-sky shape, normalised to the Solcast daily total
    shape = []
    for s in range(48):
        h = s / 2
        x = (h - 12.0) / 5.0
        shape.append(max(0.0, 1.0 - x * x))
    tot = sum(shape) * 0.5
    for s in range(48):
        moment = dt.datetime(2026, 8, 28, s // 2, 30 * (s % 2))
        slots.append(opt.Slot(
            start_min=s * 30,
            duration_h=0.5,
            import_price=tar.price_at(imp_w, moment),
            export_price=tar.price_at(exp_w, moment),
            pv_kwh=pv_total_kwh * shape[s] * 0.5 / tot,
            load_kwh=adj[s] * 0.5 / 1000.0,
        ))
    return slots


for pv in (40.5, 17.0):
    slots = build_day(pv)
    plan = opt.optimise(slots, spec, soc_start=82.6, steps=96)
    print("=" * 72)
    print(f"PV forecast {pv} kWh/day   load {sum(s.load_kwh for s in slots):.1f} kWh")
    print(f"  baseline (no battery): ${plan.baseline_cost:6.2f}")
    print(f"  optimised            : ${plan.total_cost:6.2f}")
    print(f"  saving               : ${plan.saving:6.2f}")
    cur = None
    for sp in plan.slots:
        if sp.action != cur:
            h = sp.slot.start_min // 60
            m = sp.slot.start_min % 60
            print(f"    {h:02d}:{m:02d}  -> {sp.action:17s} "
                  f"soc={sp.soc_start:5.1f}%  imp=${sp.slot.import_price:.2f} "
                  f"exp=${sp.slot.export_price:.2f}")
            cur = sp.action
    exported = -sum(s.grid_kwh for s in plan.slots if s.grid_kwh < 0)
    imported = sum(s.grid_kwh for s in plan.slots if s.grid_kwh > 0)
    print(f"  exported {exported:.1f} kWh, imported {imported:.1f} kWh, "
          f"end soc {plan.slots[-1].soc_end:.1f}%")
