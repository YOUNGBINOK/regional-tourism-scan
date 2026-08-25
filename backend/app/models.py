from math import prod
from pydantic import BaseModel, Field

class TceiInput(BaseModel):
    """All components must be percentiles in the comparable month/segment, 0..1."""
    stay_duration_percentile: float = Field(ge=0, le=1)
    overnight_share_percentile: float = Field(ge=0, le=1)
    consumption_residual_percentile: float = Field(ge=0, le=1)
    concentration_percentile: float = Field(ge=0, le=1)
    seasonal_cv_inverse_percentile: float = Field(ge=0, le=1)

class BudgetRequest(BaseModel):
    budget_billion_krw: float = Field(gt=0, le=1000)
    leak_gaps: dict[str, float]

def calculate_tcei(x: TceiInput) -> dict[str, float]:
    # S = sqrt(P(stay duration) * P(overnight share)); C is residual percentile.
    stay = (x.stay_duration_percentile * x.overnight_share_percentile) ** .5
    consumption = x.consumption_residual_percentile
    dispersion = 1 - x.concentration_percentile
    stability = x.seasonal_cv_inverse_percentile
    tcei = 100 * prod((stay, consumption, dispersion, stability)) ** .25
    return {"stay": stay, "consumption": consumption, "dispersion": dispersion, "stability": stability, "tcei": tcei}

def calculate_r_gap(actual_tcei: float, frontier_tcei: float) -> float:
    return max(0.0, frontier_tcei - actual_tcei)

def allocate_budget(request: BudgetRequest) -> dict[str, object]:
    valid = {key: max(0, value) for key, value in request.leak_gaps.items()}
    total = sum(valid.values())
    if total == 0:
        raise ValueError("At least one positive leak contribution is required.")
    priorities = {key: value / total for key, value in valid.items()}
    return {"budget_billion_krw": request.budget_billion_krw, "priorities": priorities,
            "allocations_billion_krw": {key: request.budget_billion_krw * value for key, value in priorities.items()}}
