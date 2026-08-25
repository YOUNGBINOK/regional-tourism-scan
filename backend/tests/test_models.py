from math import isclose
from app.models import TceiInput, BudgetRequest, calculate_tcei, calculate_r_gap, allocate_budget

def test_tcei_geometric_mean():
    result = calculate_tcei(TceiInput(stay_duration_percentile=.64, overnight_share_percentile=.81, consumption_residual_percentile=.5, concentration_percentile=.4, seasonal_cv_inverse_percentile=.9))
    assert isclose(result["stay"], .72)
    assert 0 < result["tcei"] <= 100

def test_gap_cannot_be_negative(): assert calculate_r_gap(80, 70) == 0

def test_budget_allocation_sums_to_input():
    result = allocate_budget(BudgetRequest(budget_billion_krw=10, leak_gaps={"stay": 51, "space": 22, "night": 14, "consumption": 13}))
    assert round(sum(result["allocations_billion_krw"].values()), 2) == 10
