from math import isclose
import pytest
from app.models import TceiInput, BudgetRequest, calculate_tcei, calculate_r_gap, allocate_budget

def test_tcei_geometric_mean():
    result = calculate_tcei(TceiInput(stay_duration_percentile=.64, overnight_share_percentile=.81, consumption_residual_percentile=.5, concentration_percentile=.4, seasonal_cv_inverse_percentile=.9))
    assert isclose(result["stay"], .72)
    assert 0 < result["tcei"] <= 100

def test_gap_cannot_be_negative(): assert calculate_r_gap(80, 70) == 0

def test_r_gap_rejects_cross_pg_category_frontier():
    # A PG-1(도심/상업) region's actual TCEI must never be scored against a
    # PG-4(농어촌) frontier — the two aren't structurally comparable, so the
    # gap would misrepresent how far behind this region actually is.
    with pytest.raises(ValueError, match="Peer Group category"):
        calculate_r_gap(60, 90, actual_pg_category="PG-1", frontier_pg_category="PG-4")

def test_r_gap_allows_same_pg_category_frontier():
    assert calculate_r_gap(60, 90, actual_pg_category="PG-2", frontier_pg_category="PG-2") == 30

def test_r_gap_without_pg_category_still_works():
    # No live pipeline supplies pg_category yet (AGENTS.md §4.6) — the guard
    # must not block the existing two-argument callers.
    assert calculate_r_gap(60, 90) == 30

def test_budget_allocation_sums_to_input():
    result = allocate_budget(BudgetRequest(budget_billion_krw=10, leak_gaps={"stay": 51, "space": 22, "night": 14, "consumption": 13}))
    assert round(sum(result["allocations_billion_krw"].values()), 2) == 10
