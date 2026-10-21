"""Small, auditable HiGHS allocation programme over Spark-produced demand."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import lil_matrix


# These are teaching assumptions in generic budget units, not clinical protocols.
RESOURCE_PARAMETERS = {
    "iv_saline": {"cost": 2., "volume": 1., "weight": 2.},
    "ns1_kits": {"cost": 4., "volume": .2, "weight": 1.},
    "ors": {"cost": .5, "volume": .1, "weight": 1.},
    "antimalarials": {"cost": 5., "volume": .1, "weight": 3.},
    "beds": {"cost": 25., "volume": 2., "weight": 5.},
    "response_teams": {"cost": 100., "volume": 5., "weight": 30.},
}
ALLOCATION_SCHEMA = "district_id string, resource string, demand double, stock double, allocation double, unmet double, baseline_allocation double, budget double, equity_floor double, unit_cost double, division string, weight double"
SCENARIO_SCHEMA = "budget double, equity_floor double, weighted_unmet double, baseline_weighted_unmet double, unmet double, total_cost double, min_population_ratio double, benefit_vs_baseline_pct double, transport_utilisation double"


def solve_allocation(demand: pd.DataFrame, budget: float, equity_floor=.2, transport=None):
    """Minimize severity-weighted shortfall with budget, equity and transport caps.

    x[d,r] >= alpha * population_share[d] * sum_d x[d,r]. The programme is
    continuous; beds and teams represent deployable capacity equivalents. The
    returned solution must be rounded through a future integer programme before
    dispatch of indivisible objects. This preserves all stated LP constraints.
    """
    if budget < 0 or not 0 <= equity_floor < 1:
        raise ValueError("Budget must be nonnegative and equity floor in [0,1)")
    table = demand.reset_index(drop=True).copy()
    n = len(table)
    if not n:
        raise ValueError("No district-resource demand rows")
    costs = table.resource.map(lambda r: RESOURCE_PARAMETERS[r]["cost"]).to_numpy(float)
    volumes = table.resource.map(lambda r: RESOURCE_PARAMETERS[r]["volume"]).to_numpy(float)
    weights = (table["weight"].to_numpy(float) if "weight" in table
               else table.resource.map(lambda r: RESOURCE_PARAMETERS[r]["weight"]).to_numpy(float))
    populations = table.drop_duplicates("district_id").set_index("district_id").population
    shares = table.district_id.map(populations/populations.sum()).to_numpy(float)
    deficit = np.maximum(table.demand.to_numpy(float)-table.stock.to_numpy(float), 0.)
    divisions = sorted(table.division.unique())
    if transport is None:
        # Capacity is fixed for all budget/equity scenarios, based on independent
        # physical need by division, with spare capacity for equitable distribution.
        transport = {division: max(10., 1.1*float(np.dot(volumes[table.division == division], deficit[table.division == division])))
                     for division in divisions}
    # x_n, u_n. A tiny allocation cost breaks cost-free oversupply ties.
    objective = np.r_[costs*1e-8, weights]
    a = lil_matrix((n+n+1+len(divisions), 2*n), dtype=float)
    b = np.zeros(a.shape[0])
    for i in range(n):
        a[i, i], a[i, n+i], b[i] = -1., -1., -deficit[i]
        indices = np.where(table.resource.to_numpy() == table.resource.iloc[i])[0]
        a[n+i, indices] = equity_floor*shares[i]
        a[n+i, i] -= 1.
    a[2*n, :n], b[2*n] = costs, budget
    for k, division in enumerate(divisions):
        indices = np.where(table.division.to_numpy() == division)[0]
        a[2*n+1+k, indices] = volumes[indices]
        b[2*n+1+k] = transport[division]
    result = linprog(objective, A_ub=a.tocsr(), b_ub=b, bounds=(0, None), method="highs")
    if not result.success:
        raise RuntimeError(f"Allocation LP failed: {result.message}")
    allocation = np.maximum(result.x[:n], 0.)
    # A common comparator distributes the same national purchasing budget by
    # population and resource net-need share. It is reduced uniformly only when a
    # transport cap binds, so it obeys the same physical constraints.
    resource_budget = {}
    for resource in table.resource.unique():
        mask = table.resource == resource
        resource_budget[resource] = float(np.dot(deficit[mask], costs[mask]))
    total_need_cost = sum(resource_budget.values())
    spend = min(budget, total_need_cost)
    baseline = np.asarray([spend * resource_budget[r]/max(total_need_cost, 1e-12)*shares[i]/costs[i]
                           for i, r in enumerate(table.resource)])
    baseline_scale = 1.
    for division in divisions:
        mask = table.division.to_numpy() == division
        used = np.dot(baseline[mask], volumes[mask])
        if used > transport[division]:
            baseline_scale = min(baseline_scale, transport[division]/used)
    baseline *= baseline_scale
    unmet = np.maximum(deficit-allocation, 0.)
    baseline_unmet = np.maximum(deficit-baseline, 0.)
    table["allocation"], table["unmet"], table["baseline_allocation"] = allocation, unmet, baseline
    table["budget"], table["equity_floor"], table["unit_cost"], table["weight"] = float(budget), float(equity_floor), costs, weights
    fairness = []
    for resource in table.resource.unique():
        mask = table.resource.to_numpy() == resource
        amount = allocation[mask].sum()
        if amount > 1e-8:
            fairness.extend((allocation[mask]/amount/shares[mask]).tolist())
    w_unmet, w_baseline = float(weights@unmet), float(weights@baseline_unmet)
    summary = {"budget": float(budget), "equity_floor": float(equity_floor), "weighted_unmet": w_unmet,
               "baseline_weighted_unmet": w_baseline, "unmet": float(unmet.sum()), "total_cost": float(costs@allocation),
               "min_population_ratio": float(min(fairness)) if fairness else 1.,
               "benefit_vs_baseline_pct": 100*(w_baseline-w_unmet)/w_baseline if w_baseline else 0.,
               "transport_utilisation": max(float(volumes[table.division == d]@allocation[table.division == d])/transport[d] for d in divisions)}
    if np.max(a.tocsr()@result.x-b) > 1e-5:
        raise AssertionError("Solver returned a constraint violation")
    return table, summary


def demand_from_forecasts(forecasts, districts, stocks):
    """Spark demand conversion uses four-week upper80 case and hospital forecasts."""
    from pyspark.sql import functions as F
    # Weekly admissions /7 times 5-day average stay gives concurrent beds. Other
    # resources are totals for the four-week procurement horizon.
    cases = forecasts.groupBy("district_id", "disease").agg(
        F.sum("upper80").alias("upper_cases"),
        F.sum(F.col("upper80")*F.col("admission_rate")).alias("upper_admissions"),
        F.max(F.col("upper80")*F.col("admission_rate")*5/7).alias("upper_beds"))
    conversion = {
        "iv_saline": F.when(F.col("disease") == "dengue", 2*F.col("upper_admissions")).otherwise(F.when(F.col("disease") == "diarrhoea", .2*F.col("upper_cases")).otherwise(0.)),
        "ns1_kits": F.when(F.col("disease") == "dengue", F.col("upper_cases")).otherwise(0.),
        "ors": F.when(F.col("disease") == "diarrhoea", 2*F.col("upper_cases")).otherwise(0.),
        "antimalarials": F.when(F.col("disease") == "malaria", F.col("upper_cases")).otherwise(0.),
        "beds": F.col("upper_beds"),
        "response_teams": F.col("upper_cases")/500,
    }
    expressions = [F.sum(expr).alias(name) for name, expr in conversion.items()]
    rows = []
    demand_wide = cases.groupBy("district_id").agg(*expressions)
    for name in conversion:
        part = demand_wide.select("district_id", F.lit(name).alias("resource"), F.col(name).alias("demand"))
        if stocks is not None:
            stock_column = (F.greatest(F.col("beds")-F.col("occupied_beds"), F.lit(0.))
                            if name == "beds" else F.col(name) if name in stocks.columns else F.lit(0.))
            stock = stocks.select("district_id", stock_column.cast("double").alias("stock"))
            part = part.join(stock, "district_id", "left").fillna({"stock": 0.})
        else:
            part = part.withColumn("stock", F.lit(0.))
        rows.append(part)
    combined = rows[0]
    for part in rows[1:]:
        combined = combined.unionByName(part)
    combined = combined.join(districts.select("district_id", "population", "division"), "district_id")
    # Only 64 districts x six resources reaches the driver-side solver.
    return combined.toPandas()


def scenario_grid(demand):
    cost = demand.resource.map(lambda r: RESOURCE_PARAMETERS[r]["cost"]).to_numpy(float)
    full_cost = float(cost@np.maximum(demand.demand-demand.stock, 0.))
    # If all stocks exceed need, the zero plan is honest; team capacity still has
    # positive need under normal scenarios. No artificial shortage is injected.
    budget_anchor = full_cost if full_cost > 1e-8 else max(1., float(cost@demand.demand.to_numpy(float)))
    budgets = [budget_anchor*f for f in (.1, .25, .5, .75, 1., 1.25)]
    allocations, summaries = [], []
    for budget in budgets:
        for alpha in (0., .2, .5, .8):
            allocation, summary = solve_allocation(demand, budget, alpha)
            allocations.append(allocation)
            summaries.append(summary)
    grid, summary = pd.concat(allocations, ignore_index=True), pd.DataFrame(summaries)
    primary = grid[(np.isclose(grid.budget, .5*budget_anchor)) & (grid.equity_floor == .2)].copy()
    return primary, grid, summary
