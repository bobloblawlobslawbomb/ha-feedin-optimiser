"""Pure optimisation core for the feed-in optimiser.

Deliberately free of Home Assistant imports so it can be unit-tested standalone.
Uses backward-induction dynamic programming over a discretised state-of-charge
grid, which is exact to the discretisation and handles the non-convexity created
by asymmetric import/export prices far more reliably than a greedy heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# Actions the battery can take in a slot.
IDLE = "idle"
CHARGE_PV = "charge_pv"
CHARGE_GRID = "charge_grid"
DISCHARGE_HOUSE = "discharge_house"
DISCHARGE_EXPORT = "discharge_export"


@dataclass(frozen=True)
class BatterySpec:
    capacity_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    reserve_soc: float = 20.0          # hard floor, percent
    max_soc: float = 100.0
    # Export power ceiling imposed by the inverter / grid connection.
    max_export_kw: float = 10.0
    # Cost charged per kWh moved through the battery. Serves two purposes:
    # it prices real cell wear, and — critically — it breaks the cost-ties that
    # occur when the export price is $0.00, where charging and discharging are
    # otherwise financially identical and the planner would happily cycle the
    # pack all night for no gain. Must be > 0.
    cycle_cost_per_kwh: float = 0.01


@dataclass(frozen=True)
class Slot:
    """One planning interval."""
    start_min: int          # minutes from horizon start
    duration_h: float
    import_price: float     # $/kWh paid to buy
    export_price: float     # $/kWh earned to sell
    pv_kwh: float           # forecast generation in this slot
    load_kwh: float         # forecast house consumption in this slot


@dataclass
class SlotPlan:
    slot: Slot
    action: str
    battery_kwh: float      # +ve = charging, -ve = discharging (at the terminals)
    grid_kwh: float         # +ve = import, -ve = export
    soc_start: float
    soc_end: float
    cost: float             # negative = profit

    def as_dict(self) -> dict:
        return {
            "start_min": self.slot.start_min,
            "action": self.action,
            "import_price": round(self.slot.import_price, 4),
            "export_price": round(self.slot.export_price, 4),
            "pv_kwh": round(self.slot.pv_kwh, 3),
            "load_kwh": round(self.slot.load_kwh, 3),
            "battery_kwh": round(self.battery_kwh, 3),
            "grid_kwh": round(self.grid_kwh, 3),
            "soc_start": round(self.soc_start, 1),
            "soc_end": round(self.soc_end, 1),
            "cost": round(self.cost, 4),
        }


@dataclass
class Plan:
    slots: list[SlotPlan] = field(default_factory=list)
    total_cost: float = 0.0
    baseline_cost: float = 0.0

    @property
    def saving(self) -> float:
        return self.baseline_cost - self.total_cost

    def as_dict(self) -> dict:
        return {
            "total_cost": round(self.total_cost, 3),
            "baseline_cost": round(self.baseline_cost, 3),
            "saving": round(self.saving, 3),
            "slots": [s.as_dict() for s in self.slots],
        }


def _slot_cost(
    slot: Slot,
    batt_kwh: float,
    spec: BatterySpec,
) -> tuple[float, float] | None:
    """Return (cost, grid_kwh) for moving ``batt_kwh`` at the battery terminals.

    ``batt_kwh`` is energy delivered into (+) or out of (-) storage. Returns None
    when the move violates a power limit.
    """
    if batt_kwh >= 0:
        # Drawing from AC side to store batt_kwh needs more because of losses.
        ac_kwh = batt_kwh / spec.charge_efficiency
        if ac_kwh / slot.duration_h > spec.max_charge_kw + 1e-9:
            return None
    else:
        ac_kwh = batt_kwh * spec.discharge_efficiency
        if -ac_kwh / slot.duration_h > spec.max_discharge_kw + 1e-9:
            return None

    grid_kwh = slot.load_kwh - slot.pv_kwh + ac_kwh

    curtailed_kwh = 0.0
    if grid_kwh < 0 and (-grid_kwh) / slot.duration_h > spec.max_export_kw + 1e-9:
        # Export ceiling reached. A real inverter curtails PV rather than
        # failing, so model the spill instead of declaring the slot infeasible
        # (which would make PV-heavy slots unsolvable and crash the DP).
        allowed = spec.max_export_kw * slot.duration_h
        curtailed_kwh = (-grid_kwh) - allowed
        grid_kwh = -allowed

    if grid_kwh >= 0:
        cost = grid_kwh * slot.import_price
    else:
        cost = grid_kwh * slot.export_price  # negative -> revenue
    # Wear/tie-break term, always non-negative.
    cost += abs(batt_kwh) * spec.cycle_cost_per_kwh
    # Tiny penalty on spilled solar so that, all else equal, the planner prefers
    # to store or use energy rather than throw it away.
    cost += curtailed_kwh * 1e-4
    return cost, grid_kwh


def _classify(slot: Slot, batt_kwh: float, grid_kwh: float) -> str:
    if abs(batt_kwh) < 1e-6:
        return IDLE
    if batt_kwh > 0:
        # Charging: is it coming from surplus PV or bought from the grid?
        return CHARGE_GRID if grid_kwh > 1e-6 else CHARGE_PV
    return DISCHARGE_EXPORT if grid_kwh < -1e-6 else DISCHARGE_HOUSE


def _polish(plan: "Plan", spec: BatterySpec) -> None:
    """Remove discretisation artefacts from a bucket-quantised trajectory.

    The DP moves the battery in whole SOC buckets. When a slot's genuine demand
    is smaller than one bucket (typical overnight, where load is ~0.1 kWh against
    a ~0.4 kWh bucket), the only way the DP can serve load from storage is to
    move a full bucket and spill the remainder to the grid — exporting real
    energy at $0.00/kWh. This pass walks the plan with a continuous SOC and
    trims each slot's battery flow to the smallest move that is still
    economically justified, which is always feasible because it is strictly
    closer to idle than the move the DP already validated.
    """
    soc = plan.slots[0].soc_start if plan.slots else 0.0
    cap = spec.capacity_kwh
    total = 0.0

    for sp in plan.slots:
        slot = sp.slot
        batt = sp.battery_kwh
        residual = slot.load_kwh - slot.pv_kwh   # +ve = house needs energy

        if batt < 0:
            # Discharging. Exporting only pays if it beats the wear it costs.
            export_worthwhile = slot.export_price > spec.cycle_cost_per_kwh
            if not export_worthwhile:
                # Serve load, but never push surplus out for nothing. Delivering
                # ``residual`` kWh to the AC side needs residual/eff from the
                # cells, and the flow is negative because it leaves the battery.
                floor = -max(0.0, residual) / spec.discharge_efficiency
                if batt < floor:
                    batt = floor
        elif batt > 0:
            # Charging from the grid only pays when the import price justifies
            # it; charging from surplus PV is free and always kept.
            if slot.import_price > 0:
                surplus = max(0.0, -residual)
                free = surplus * spec.charge_efficiency
                if batt > free:
                    # Grid-charging portion — keep it, the DP judged it worth
                    # paying for, but re-check it against the wear cost.
                    if slot.import_price + spec.cycle_cost_per_kwh <= 0:
                        batt = free

        # Clamp to SOC limits, continuously.
        soc_delta = batt / cap * 100.0
        new_soc = soc + soc_delta
        if new_soc > spec.max_soc:
            batt = (spec.max_soc - soc) / 100.0 * cap
        elif new_soc < spec.reserve_soc:
            batt = (spec.reserve_soc - soc) / 100.0 * cap

        res = _slot_cost(slot, batt, spec)
        if res is None:
            batt = 0.0
            res = _slot_cost(slot, 0.0, spec)
        cost, grid_kwh = res

        sp.soc_start = soc
        sp.battery_kwh = batt
        sp.grid_kwh = grid_kwh
        sp.cost = cost
        sp.action = _classify(slot, batt, grid_kwh)
        soc = soc + batt / cap * 100.0
        sp.soc_end = soc
        total += cost

    plan.total_cost = total


def optimise(
    slots: Sequence[Slot],
    spec: BatterySpec,
    soc_start: float,
    steps: int = 100,
    terminal_value: float | None = None,
) -> Plan:
    """Cost-minimising dispatch by backward induction.

    ``steps`` is the number of SOC buckets; 100 gives ~0.5 kWh resolution on a
    48 kWh pack, which is well inside the noise of the underlying forecasts.
    ``terminal_value`` values leftover energy at the horizon ($/kWh) to stop the
    planner dumping the battery in the final slot. Defaults to the best import
    price seen in the window, i.e. energy is worth at least what it displaces.
    """
    n = len(slots)
    if n == 0:
        return Plan()

    if terminal_value is None:
        terminal_value = max((s.import_price for s in slots), default=0.0)

    lo = spec.reserve_soc
    hi = spec.max_soc
    if hi <= lo:
        raise ValueError("max_soc must exceed reserve_soc")

    # SOC grid in percent.
    grid_soc = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    kwh_per_step = spec.capacity_kwh * (hi - lo) / 100.0 / steps

    INF = float("inf")
    # value[i] = min cost-to-go from bucket i at the current stage.
    value = [-(grid_soc[i] - lo) / 100.0 * spec.capacity_kwh * terminal_value
             for i in range(steps + 1)]
    choice: list[list[int]] = []

    for t in range(n - 1, -1, -1):
        slot = slots[t]
        nxt = value
        cur = [INF] * (steps + 1)
        pick = [0] * (steps + 1)

        # Feasible bucket deltas, bounded by power limits, to keep this O(steps*k).
        max_up = int(spec.max_charge_kw * slot.duration_h
                     * spec.charge_efficiency / kwh_per_step) + 1
        max_dn = int(spec.max_discharge_kw * slot.duration_h
                     / spec.discharge_efficiency / kwh_per_step) + 1

        for i in range(steps + 1):
            best = INF
            best_j = i
            for d in range(-max_dn, max_up + 1):
                j = i + d
                if j < 0 or j > steps:
                    continue
                if nxt[j] == INF:
                    continue
                res = _slot_cost(slot, d * kwh_per_step, spec)
                if res is None:
                    continue
                total = res[0] + nxt[j]
                if total < best:
                    best = total
                    best_j = j
            cur[i] = best
            pick[i] = best_j
        value = cur
        choice.append(pick)

    choice.reverse()

    # Forward pass from the actual starting SOC.
    start_idx = min(range(steps + 1),
                    key=lambda i: abs(grid_soc[i] - max(soc_start, lo)))
    plan = Plan()
    idx = start_idx
    for t, slot in enumerate(slots):
        j = choice[t][idx]
        batt = (j - idx) * kwh_per_step
        res = _slot_cost(slot, batt, spec)
        if res is None:      # numerically unreachable; hold instead
            j, batt = idx, 0.0
            res = _slot_cost(slot, 0.0, spec)
        cost, grid_kwh = res
        plan.slots.append(SlotPlan(
            slot=slot,
            action=_classify(slot, batt, grid_kwh),
            battery_kwh=batt,
            grid_kwh=grid_kwh,
            soc_start=grid_soc[idx],
            soc_end=grid_soc[j],
            cost=cost,
        ))
        plan.total_cost += cost
        idx = j

    _polish(plan, spec)
    plan.baseline_cost = baseline_cost(slots, spec)
    return plan


def baseline_cost(slots: Sequence[Slot], spec: BatterySpec) -> float:
    """Cost with no battery at all — PV offsets load, surplus is exported."""
    total = 0.0
    for s in slots:
        grid = s.load_kwh - s.pv_kwh
        total += grid * (s.import_price if grid >= 0 else s.export_price)
    return total
