from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from .models import TceiInput, BudgetRequest, calculate_tcei, calculate_r_gap, allocate_budget
from .data_sources import provider_statuses, fetch_provider_json, fetch_kto_regional_visitors
from .settings import cors_origin_list

app = FastAPI(title="R-GAP API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=cors_origin_list(), allow_methods=["*"], allow_headers=["*"])

class ProviderFetchRequest(BaseModel):
    provider: str = Field(pattern="^(kto_tourism_datalab|public_data_portal|local_finance365)$")
    endpoint: str = Field(min_length=1)
    params: dict[str, str] = {}

class VisitorFetchRequest(BaseModel):
    scope: str = Field(pattern="^(metro|local)$")
    start_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260701"])
    end_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260731"])
    page_no: int = Field(default=1, ge=1)
    num_rows: int = Field(default=1000, ge=1, le=1000)

@app.get("/health")
def health(): return {"status": "ok", "service": "R-GAP API"}

@app.get("/v1/data-sources/status")
def data_source_status():
    """Safe status only: API credentials are never returned to the client."""
    return {"sources": [status.__dict__ for status in provider_statuses()]}

@app.post("/v1/data-sources/fetch")
async def data_source_fetch(payload: ProviderFetchRequest):
    """Server-side provider adapter. Supply endpoint/filters; credentials come only from .env."""
    try: return await fetch_provider_json(payload.provider, payload.endpoint, payload.params)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error))
    except Exception as error: raise HTTPException(status_code=502, detail=f"Provider request failed: {error}")

@app.post("/v1/data-sources/kto/regional-visitors")
async def kto_regional_visitors(payload: VisitorFetchRequest):
    """KTO regional visitor GW: metro=metcoRegnVisitrDDList, local=locgoRegnVisitrDDList."""
    try:
        return await fetch_kto_regional_visitors(payload.scope, payload.start_ymd, payload.end_ymd, payload.page_no, payload.num_rows)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error))
    except Exception as error: raise HTTPException(status_code=502, detail=f"KTO visitor request failed: {error}")

@app.post("/v1/metrics/tcei")
def tcei(payload: TceiInput): return calculate_tcei(payload)

@app.post("/v1/regions/{region_code}/r-gap")
def r_gap(region_code: str, actual_tcei: float, frontier_tcei: float):
    return {"region_code": region_code, "actual_tcei": actual_tcei, "frontier_tcei": frontier_tcei,
            "r_gap": calculate_r_gap(actual_tcei, frontier_tcei)}

@app.post("/v1/budget/portfolio")
def portfolio(payload: BudgetRequest):
    try: return allocate_budget(payload)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error))
