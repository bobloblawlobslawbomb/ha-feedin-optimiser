"""Constants for the Feed-in Optimiser integration."""

DOMAIN = "feedin_optimiser"

# --- config keys -----------------------------------------------------------
CONF_SOC_SENSOR = "soc_sensor"
CONF_CAPACITY_KWH = "capacity_kwh"
CONF_PV_FORECAST_TODAY = "pv_forecast_today"
CONF_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow"
CONF_LOAD_SENSOR = "load_sensor"
CONF_MAX_CHARGE_KW = "max_charge_kw"
CONF_MAX_DISCHARGE_KW = "max_discharge_kw"
CONF_MAX_EXPORT_KW = "max_export_kw"
CONF_RESERVE_SOC = "reserve_soc"
CONF_CYCLE_COST = "cycle_cost_per_kwh"
CONF_IMPORT_WINDOWS = "import_windows"
CONF_EXPORT_WINDOWS = "export_windows"
CONF_HORIZON_HOURS = "horizon_hours"

# --- defaults (derived from the user's Globird plan + Neovolt hardware) -----
DEFAULT_CAPACITY_KWH = 47.88
DEFAULT_MAX_CHARGE_KW = 10.0
DEFAULT_MAX_DISCHARGE_KW = 10.0
DEFAULT_MAX_EXPORT_KW = 10.0
DEFAULT_RESERVE_SOC = 20.0
DEFAULT_CYCLE_COST = 0.01
# 48h so the planner can see past tomorrow's free-power refill. This is what
# makes exporting into the evening Super window profitable rather than hoarding.
DEFAULT_HORIZON_HOURS = 48

SLOT_MINUTES = 30
UPDATE_INTERVAL_MINUTES = 5

ATTR_PLAN = "plan"
ATTR_NEXT_ACTION = "next_action"
ATTR_SAVING = "estimated_saving"
