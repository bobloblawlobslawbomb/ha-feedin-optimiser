"""Test HA-facing modules with stubbed homeassistant imports.

Covers the failure modes the integration skill flags:
  * config-flow selectors that render a black dialog without a default
  * forecast/slot construction against the real tariff windows
"""

import datetime as dt
import importlib.util
import sys
import types

ROOT = "/home/hermes/ha-feedin-optimiser/custom_components/feedin_optimiser/"
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------- HA stubs
def mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


ha = mod("homeassistant")
mod("homeassistant.helpers")
mod("homeassistant.util")

ce = mod("homeassistant.config_entries")


class ConfigEntry:
    pass


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kw):
        super().__init_subclass__(**kw)


class OptionsFlow:
    pass


ce.ConfigEntry = ConfigEntry
ce.ConfigFlow = ConfigFlow
ce.OptionsFlow = OptionsFlow

core = mod("homeassistant.core")
core.HomeAssistant = type("HomeAssistant", (), {})
core.callback = lambda f: f

sel = mod("homeassistant.helpers.selector")


class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Sel:
    def __init__(self, config=None):
        self.config = config

    def __call__(self, value):
        return value


sel.EntitySelector = _Sel
sel.EntitySelectorConfig = _Cfg
sel.NumberSelector = _Sel
sel.NumberSelectorConfig = _Cfg
sel.NumberSelectorMode = types.SimpleNamespace(BOX="box")
sel.SelectSelector = _Sel
sel.SelectSelectorConfig = _Cfg
sel.SelectSelectorMode = types.SimpleNamespace(LIST="list")

# ------------------------------------------------------------- load modules
def load(name, path=None):
    spec = importlib.util.spec_from_file_location(
        f"custom_components.feedin_optimiser.{name}", ROOT + (path or name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


pkg = types.ModuleType("custom_components.feedin_optimiser")
pkg.__path__ = [ROOT]
sys.modules["custom_components"] = types.ModuleType("custom_components")
sys.modules["custom_components"].__path__ = ["/home/hermes/ha-feedin-optimiser/custom_components"]
sys.modules["custom_components.feedin_optimiser"] = pkg

optimiser = load("optimiser")
tariff = load("tariff")
const = load("const")
pkg.optimiser, pkg.tariff, pkg.const = optimiser, tariff, const
forecast = load("forecast")
pkg.forecast = forecast

print("\n[A] config flow schema")
try:
    import voluptuous as vol  # noqa: F401
    have_vol = True
except ImportError:
    have_vol = False

if not have_vol:
    print("  SKIP  voluptuous not installed locally")
else:
    cf = load("config_flow")
    s = cf.schema({})
    # Replicate HA frontend computeInitialHaFormData: every field must either be
    # optional or carry a default, else the config dialog renders black.
    missing = []
    for key in s.schema:
        has_default = getattr(key, "default", None) is not None
        if not has_default:
            missing.append(str(key))
    check("every field has a default (no black dialog)", not missing, str(missing))
    check("schema builds with saved values", cf.schema(
        {const.CONF_SOC_SENSOR: "sensor.x", const.CONF_CAPACITY_KWH: 47.88}) is not None)

print("\n[B] forecast slot construction")
imp = tariff.parse_windows(tariff.DEFAULT_IMPORT_WINDOWS)
exp = tariff.parse_windows(tariff.DEFAULT_EXPORT_WINDOWS)
profile = [300.0] * 48
start = dt.datetime(2026, 8, 28, 10, 0)
slots = forecast.build_slots(start, 48, 30, imp, exp, [40.5, 17.0, 17.0], profile)
check("slot count matches horizon", len(slots) == 96, f"{len(slots)} slots")
pv_sum = sum(s.pv_kwh for s in slots)
check("PV energy is finite and positive", 0 < pv_sum < 200, f"{pv_sum:.1f} kWh")
check("no PV at midnight", all(
    s.pv_kwh == 0 for s in slots
    if 0 <= ((start + dt.timedelta(minutes=s.start_min)).hour) < 5))

first = slots[0]
# Free power runs 11:00-14:00, so 10:00 is still Shoulder.
check("10:00 is Shoulder import", abs(first.import_price - 0.44) < 1e-9,
      f"${first.import_price}")
noon = [s for s in slots
        if (start + dt.timedelta(minutes=s.start_min)).hour == 12][0]
check("12:00 is free power", abs(noon.import_price - 0.0) < 1e-9,
      f"${noon.import_price}")
eve = [s for s in slots
       if (start + dt.timedelta(minutes=s.start_min)).hour == 19][0]
check("19:00 is Super export", abs(eve.export_price - 0.10) < 1e-9,
      f"${eve.export_price}")

print("\n[C] end-to-end optimise on constructed slots")
spec = optimiser.BatterySpec(capacity_kwh=47.88, max_charge_kw=10, max_discharge_kw=10,
                             max_export_kw=10, reserve_soc=20.0)
plan = optimiser.optimise(slots, spec, soc_start=82.6, steps=96)
check("plan covers horizon", len(plan.slots) == 96)
check("SOC respects reserve", min(s.soc_end for s in plan.slots) >= 20.0 - 1e-6)
check("SOC never exceeds 100", max(s.soc_end for s in plan.slots) <= 100.0 + 1e-6)
sup = -sum(s.grid_kwh for s in plan.slots
           if s.grid_kwh < 0 and s.slot.export_price >= 0.10)
check("uses the Super export window", sup > 1.0, f"{sup:.1f} kWh")

print("\n" + ("ALL TESTS PASSED" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
