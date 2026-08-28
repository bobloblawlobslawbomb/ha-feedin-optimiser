"""Horizon test: does a longer horizon unlock the $0.10 Super export window?

With a 24h horizon the planner values leftover energy at the peak import price
and hoards it. Extending the horizon past tomorrow's free-power window lets it
see that the pack is refilled for free at 11:00, so discharging into tonight's
18:00-21:00 Super window at $0.10/kWh is genuinely profitable.
"""
import datetime as dt
import importlib.util
import json
import sys

BASE = "/home/hermes/ha-feedin-optimiser/custom_components/feedin_optimiser/"

def load(n):
    s = importlib.util.spec_from_file_location(n, BASE + n + ".py")
    m = importlib.util.module_from_spec(s); sys.modules[n] = m
    s.loader.exec_module(m); return m

opt = load("optimiser"); tar = load("tariff")
imp_w = tar.parse_windows(tar.DEFAULT_IMPORT_WINDOWS)
exp_w = tar.parse_windows(tar.DEFAULT_EXPORT_WINDOWS)

load_w = json.load(open("/tmp/load_profile.json"))
adj = [1500.0 if 22 <= i < 28 else (v or 300.0) for i, v in enumerate(load_w)]

spec = opt.BatterySpec(capacity_kwh=47.88, max_charge_kw=10.0, max_discharge_kw=10.0,
                       reserve_soc=20.0, max_export_kw=10.0)

shape = [max(0.0, 1.0 - ((s / 2 - 12.0) / 5.0) ** 2) for s in range(48)]
tot = sum(shape) * 0.5


def build(days_pv, start_slot=0):
    """Slots from `start_slot` (today) across len(days_pv) days."""
    slots = []
    n = 0
    for d, pv in enumerate(days_pv):
        for s in range(48):
            if d == 0 and s < start_slot:
                continue
            moment = dt.datetime(2026, 8, 28, s // 2, 30 * (s % 2))
            slots.append(opt.Slot(
                start_min=n * 30, duration_h=0.5,
                import_price=tar.price_at(imp_w, moment),
                export_price=tar.price_at(exp_w, moment),
                pv_kwh=pv * shape[s] * 0.5 / tot,
                load_kwh=adj[s] * 0.5 / 1000.0))
            n += 1
    return slots


def summarise(tag, slots, horizon_slots, soc0):
    plan = opt.optimise(slots[:horizon_slots], spec, soc_start=soc0, steps=96)
    # Only score the first 24h so the comparison is like-for-like.
    first_day = [sp for sp in plan.slots if sp.slot.start_min < 24 * 60]
    cost = sum(sp.cost for sp in first_day)
    sup = -sum(sp.grid_kwh for sp in first_day
               if sp.grid_kwh < 0 and sp.slot.export_price >= 0.10)
    waste = -sum(sp.grid_kwh for sp in first_day
                 if sp.grid_kwh < 0 and sp.slot.export_price == 0.0)
    rev = -sum(sp.grid_kwh * sp.slot.export_price for sp in first_day
               if sp.grid_kwh < 0)
    print(f"  {tag:22s} day1 cost ${cost:6.2f} | "
          f"Super-window export {sup:5.1f} kWh (${rev:4.2f}) | "
          f"$0.00 export {waste:5.1f} kWh")
    return plan


for label, pv in (("high PV (40.5)", 40.5), ("low PV (17.0)", 17.0)):
    print(f"\n=== {label}, start 00:00 @ 82.6% SOC")
    slots2 = build([pv, pv])
    summarise("24h horizon", slots2, 48, 82.6)
    summarise("48h horizon", slots2, 96, 82.6)

# Most realistic: plan from *now* (mid-morning) with tomorrow's real forecast.
print("\n=== realistic: today 40.5 kWh, tomorrow 17.0 kWh, from 10:00, SOC 82.6%")
slots2 = build([40.5, 17.0], start_slot=20)
summarise("24h horizon", slots2, 48, 82.6)
summarise("48h horizon", slots2, 76, 82.6)
