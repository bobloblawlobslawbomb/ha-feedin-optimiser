"""Regression tests for the optimiser core. Run: python3 tests/test_optimiser.py"""

import datetime as dt
import importlib.util
import itertools
import sys

BASE = "/home/hermes/ha-feedin-optimiser/custom_components/feedin_optimiser/"


def load(n):
    s = importlib.util.spec_from_file_location(n, BASE + n + ".py")
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


opt = load("optimiser")
tar = load("tariff")

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def spec(**kw):
    base = dict(capacity_kwh=48.0, max_charge_kw=10.0, max_discharge_kw=10.0,
                reserve_soc=20.0, max_export_kw=10.0)
    base.update(kw)
    return opt.BatterySpec(**base)


def slot(imp, exp, pv=0.0, load=0.1, i=0, dur=0.5):
    return opt.Slot(start_min=i * 30, duration_h=dur, import_price=imp,
                    export_price=exp, pv_kwh=pv, load_kwh=load)


print("\n[1] never exports when export price is zero")
s = spec()
slots = [slot(0.44, 0.00, load=0.1, i=i) for i in range(8)]
p = opt.optimise(slots, s, soc_start=80.0, steps=96)
waste = -sum(x.grid_kwh for x in p.slots if x.grid_kwh < 0)
check("no export at $0.00", waste < 1e-6, f"exported {waste:.4f} kWh")

print("\n[2] respects the reserve SOC floor")
s = spec(reserve_soc=35.0)
slots = [slot(0.55, 0.50, load=5.0, i=i) for i in range(10)]
p = opt.optimise(slots, s, soc_start=60.0, steps=96)
lo = min(x.soc_end for x in p.slots)
check("SOC never below reserve", lo >= 35.0 - 1e-6, f"min soc {lo:.2f}%")

print("\n[3] discharges to serve load instead of importing at peak")
# The battery is only worth discharging if the energy can be replaced more
# cheaply later. A flat price with no cheap refill makes holding optimal, so the
# horizon must include the free-power window for this to be a real test.
s = spec()
slots = ([slot(0.55, 0.00, load=1.0, i=i) for i in range(6)]        # peak
         + [slot(0.00, 0.00, load=0.2, i=6 + i) for i in range(6)])  # free refill
p = opt.optimise(slots, s, soc_start=90.0, steps=96)
imported = sum(x.grid_kwh for x in p.slots[:6] if x.grid_kwh > 0)
check("no peak import with a full battery", imported < 0.05, f"imported {imported:.3f} kWh")

print("\n[4] charges during free power")
s = spec()
slots = ([slot(0.00, 0.00, load=0.2, i=i) for i in range(6)]
         + [slot(0.55, 0.00, load=1.0, i=6 + i) for i in range(6)])
p = opt.optimise(slots, s, soc_start=40.0, steps=96)
free_charge = sum(x.battery_kwh for x in p.slots[:6] if x.battery_kwh > 0)
check("charges while import is free", free_charge > 1.0, f"charged {free_charge:.2f} kWh")

print("\n[5] exports into a high-priced window when refill is free")
s = spec()
slots = ([slot(0.55, 0.10, load=0.2, i=i) for i in range(6)]       # super export
         + [slot(0.00, 0.00, load=0.2, i=6 + i) for i in range(6)])  # free refill
p = opt.optimise(slots, s, soc_start=90.0, steps=96, terminal_value=0.0)
sup = -sum(x.grid_kwh for x in p.slots[:6] if x.grid_kwh < 0)
check("exports into $0.10 window", sup > 1.0, f"exported {sup:.2f} kWh")

print("\n[6] power limits are respected")
# Force real movement: free power now, expensive later, so it must charge hard.
s = spec(max_charge_kw=3.0, max_discharge_kw=3.0)
slots = ([slot(0.00, 0.00, load=0.1, i=i) for i in range(8)]
         + [slot(0.55, 0.00, load=2.0, i=8 + i) for i in range(8)])
p = opt.optimise(slots, s, soc_start=30.0, steps=96)
moved = sum(abs(x.battery_kwh) for x in p.slots)
worst = max(abs(x.battery_kwh) / x.slot.duration_h for x in p.slots)
check("battery actually moved", moved > 1.0, f"moved {moved:.2f} kWh")
check("battery power within limit", worst <= 3.0 / 0.95 + 1e-6, f"peak {worst:.2f} kW")

print("\n[7] export power ceiling is respected")
s = spec(max_export_kw=2.0)
slots = [slot(0.10, 0.30, pv=6.0, load=0.1, i=i) for i in range(6)]
p = opt.optimise(slots, s, soc_start=100.0, steps=96)
worst = max((-x.grid_kwh) / x.slot.duration_h for x in p.slots if x.grid_kwh < 0)
check("export within ceiling", worst <= 2.0 + 1e-6, f"peak export {worst:.2f} kW")

print("\n[8] optimality vs brute force (small instance, continuous polish allowed)")
s = spec(capacity_kwh=10.0, max_charge_kw=5.0, max_discharge_kw=5.0, max_export_kw=5.0)
STEPS = 20
prices = [(0.44, 0.00), (0.00, 0.00), (0.55, 0.02), (0.55, 0.10), (0.44, 0.00)]
slots = [slot(ip, ep, pv=(1.5 if i == 1 else 0.0), load=0.3, i=i)
         for i, (ip, ep) in enumerate(prices)]
kps = s.capacity_kwh * (s.max_soc - s.reserve_soc) / 100.0 / STEPS
term = max(x.import_price for x in slots)
start_idx = max(0, min(STEPS, round((82.6 - s.reserve_soc)
                                    / (s.max_soc - s.reserve_soc) * STEPS)))
best = None
for traj in itertools.product(range(STEPS + 1), repeat=len(slots)):
    idx, total, ok = start_idx, 0.0, True
    for t, j in enumerate(traj):
        r = opt._slot_cost(slots[t], (j - idx) * kps, s)
        if r is None:
            ok = False
            break
        total += r[0]
        idx = j
    if ok:
        total += -(idx * kps) * term
        if best is None or total < best - 1e-12:
            best = total
p = opt.optimise(slots, s, soc_start=82.6, steps=STEPS)
dp = p.total_cost + -((p.slots[-1].soc_end - s.reserve_soc) / 100.0 * s.capacity_kwh) * term
check("DP no worse than brute force", dp <= best + 1e-6, f"dp={dp:.6f} bf={best:.6f}")

print("\n[9] tariff windows wrap midnight correctly")
w = tar.parse_windows(tar.DEFAULT_IMPORT_WINDOWS)
cases = [(2, 0.44, "Shoulder"), (12, 0.00, "Free"), (15, 0.44, "Shoulder"),
         (18, 0.55, "Peak"), (23, 0.44, "Shoulder")]
for hour, want, lbl in cases:
    got = tar.price_at(w, dt.datetime(2026, 8, 28, hour, 0))
    check(f"{hour:02d}:00 -> ${want:.2f} ({lbl})", abs(got - want) < 1e-9, f"got ${got:.2f}")

print("\n[10] empty horizon is handled")
p = opt.optimise([], spec(), soc_start=50.0)
check("empty plan", p.slots == [] and p.total_cost == 0.0)

print("\n" + ("ALL TESTS PASSED" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
