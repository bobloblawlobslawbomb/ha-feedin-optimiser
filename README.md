# Feed-in Optimiser

A Home Assistant integration that plans home-battery charging and discharging to
minimise your electricity bill under a time-of-use tariff with paid feed-in
windows.

**Advisory only.** It publishes a recommended plan as sensors. It never writes to
your battery. You decide whether to act on it (manually, or with your own
automations reading the sensors).

## Why a planner rather than a schedule

With a flat schedule you either export too early (giving away energy you would
have used at peak import rates) or too late (missing the high feed-in window).
The right decision depends on tomorrow's solar forecast, your current state of
charge, and the relative value of stored energy — which changes daily.

The key economic insight for a tariff like Globird's:

| Use of 1 kWh from the battery | Value |
|---|---|
| Avoid a peak import (16:00–23:00) | **$0.55** |
| Export in the Super window (18:00–21:00) | **$0.10** |
| Export outside a paid window | **$0.00** |

Stored energy is worth over five times more displacing an import than it is
exported. So exporting is only correct for energy you genuinely cannot use before
the battery is refilled cheaply — which for this tariff means the free-power
window at 11:00 the next day.

That is why the **planning horizon must extend past tomorrow's cheap-power
window**. With a 24-hour horizon the planner values leftover charge at the peak
import price and hoards it, never exporting at all. Extending to 48 hours lets it
see the free refill and unlock the paid export window. On this author's real data
that single change moved day-one cost from **+$3.12 to −$1.08**.

## How it works

Backward-induction dynamic programming over a discretised state-of-charge grid.
This is exact to the discretisation and, unlike a greedy heuristic, handles the
non-convexity created by asymmetric import/export prices. It is verified against
brute-force enumeration in the test suite.

A continuous "polish" pass then trims each slot's battery flow. Without it, the
bucket size (~0.4 kWh on a 48 kWh pack) exceeds the overnight load (~0.1 kWh per
half hour), and the planner is forced to dump the remainder to the grid at
$0.00/kWh — real energy given away as a pure discretisation artefact.

A small `cycle_cost_per_kwh` prices battery wear and, importantly, breaks the
cost-ties that occur when the export price is zero. Without it, charging and
discharging are financially identical and the planner will happily cycle the pack
all night for no gain.

## Configuration

All via the UI. Key options:

| Option | Notes |
|---|---|
| Battery SOC sensor | Use the sensor that reflects your **whole** system. If you have multiple banks, an inverter's own SOC may only cover its own pack. |
| Usable capacity | Usable, not nameplate. |
| House load sensor | Used to learn your usage pattern from 14 days of recorder history. |
| Solar forecast today/tomorrow | e.g. Solcast's daily total sensors. |
| Reserve SOC | A hard floor; the plan never discharges below it. |
| Planning horizon | **48 h recommended.** See above. |
| Cycle cost | ~0.01 AUD/kWh is a sensible start. Must be > 0. |

Tariff windows default to the Globird plan described above and can be overridden.

## Entities

- `sensor.feed_in_optimiser_recommended_action` — what to do right now, with the
  full upcoming schedule in its attributes.
- `sensor.feed_in_optimiser_estimated_saving` — versus having no battery.
- `sensor.feed_in_optimiser_next_action_change` — when the recommendation changes.
- `sensor.feed_in_optimiser_planned_export_revenue` — planned feed-in earnings.

## Tests

```bash
python3 tests/test_optimiser.py    # core logic incl. brute-force optimality
python3 tests/test_ha_modules.py   # config flow + forecast (stubbed HA)
```

## Caveats

- The solar shape is a smooth parabola scaled to the Solcast daily total. The
  daily total carries the forecasting skill; the intraday shape is approximate.
- Load is forecast from a 14-day median by time of day. Median is deliberate: it
  resists the EV/battery charging spikes that would otherwise be double-counted
  as house load.
- Advisory only, by design. Verify the plan against your own judgement before
  automating against it.
