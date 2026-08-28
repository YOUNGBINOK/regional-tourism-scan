import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from .models import TceiInput, BudgetRequest, calculate_tcei, calculate_r_gap, allocate_budget
from .data_sources import (provider_statuses, fetch_provider_json, fetch_kto_regional_visitors,
                           fetch_kto_catalog_service, fetch_kto_configured_service,
                           normalize_kto_xml, kto_catalog_with_readiness, build_live_visitor_snapshot)
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

class KtoMetricRequest(BaseModel):
    dataset: str = Field(pattern="^(demand_intensity|tourism_diversity)$")
    metric: str
    params: dict[str, str] = {}

class KtoAreaMetricRequest(BaseModel):
    area_cd: str = Field(pattern=r"^\d{5}$", examples=["47130"])
    base_ym: str = Field(pattern=r"^\d{6}$", examples=["202607"])
    page_no: int = Field(default=1, ge=1)
    num_rows: int = Field(default=1000, ge=1, le=1000)

class KtoRegionSnapshotRequest(KtoAreaMetricRequest):
    start_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260701"])
    end_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260731"])

class KtoConfiguredDatasetRequest(BaseModel):
    params: dict[str, str] = {}

class LiveVisitorRequest(BaseModel):
    area_cd: str = Field(pattern=r"^\d{5}$", examples=["47130"])
    base_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260701"])

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

@app.get("/v1/data-sources/kto/catalog")
def kto_catalog():
    """Official KTO datasets mapped to R-GAP; no secret is included."""
    return {"datasets": kto_catalog_with_readiness(), "key_source": "KTO_TOURISM_DATALAB_API_KEY"}

@app.post("/v1/data-sources/kto/metric")
async def kto_metric(payload: KtoMetricRequest):
    try: return await fetch_kto_catalog_service(payload.dataset, payload.metric, payload.params)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error))
    except Exception as error: raise HTTPException(status_code=502, detail=f"KTO metric request failed: {error}")

async def area_metric(dataset: str, metric: str, payload: KtoAreaMetricRequest):
    index_filters = {
        ("demand_intensity", "stay"): {"tarSjrnDsIxCd": "21"},
        ("demand_intensity", "spend"): {"tarExpDsIxCd": "22"},
        ("tourism_diversity", "visitor"): {"touDivIxCd": "31"},
        ("tourism_diversity", "spend"): {"expDivIxCd": "32"},
        ("tourism_diversity", "international"): {"intlDivIxCd": "33"},
    }
    region_params = {"areaCd": payload.area_cd[:2], "baseYm": payload.base_ym}
    # The visitor feed exposes Jeonju as 52110, while the analytical feeds
    # expose its two districts (52111/52113). Fetch the province slice so the
    # live analysis can aggregate only those two district records.
    if payload.area_cd != "52110":
        region_params["signguCd"] = payload.area_cd
    raw = await fetch_kto_catalog_service(dataset, metric, {
        # KTO requires the 2-digit province code and 5-digit municipality
        # code in separate fields. Passing the municipality as areaCd returns
        # a successful but empty response.
        **region_params,
        "pageNo": str(payload.page_no), "numOfRows": str(payload.num_rows), "_type": "json",
        **index_filters.get((dataset, metric), {}),
    })
    return normalize_kto_xml(raw)

@app.post("/v1/data-sources/kto/demand-intensity/{metric}")
async def demand_intensity(metric: str, payload: KtoAreaMetricRequest):
    if metric not in ("stay", "spend"): raise HTTPException(status_code=422, detail="metric must be stay or spend")
    try: return await area_metric("demand_intensity", metric, payload)
    except Exception as error: raise HTTPException(status_code=502, detail=f"KTO demand-intensity request failed: {error}")

@app.post("/v1/data-sources/kto/tourism-diversity/{metric}")
async def tourism_diversity(metric: str, payload: KtoAreaMetricRequest):
    if metric not in ("visitor", "spend", "international"): raise HTTPException(status_code=422, detail="metric must be visitor, spend, or international")
    try: return await area_metric("tourism_diversity", metric, payload)
    except Exception as error: raise HTTPException(status_code=502, detail=f"KTO diversity request failed: {error}")

@app.post("/v1/data-sources/kto/configured/{dataset}")
async def kto_configured_dataset(dataset: str, payload: KtoConfiguredDatasetRequest):
    """Use the two newly approved products once their official operation name is set in .env."""
    try:
        return normalize_kto_xml(await fetch_kto_configured_service(dataset, payload.params))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"KTO configured-dataset request failed: {error}")

@app.post("/v1/data-sources/kto/region-snapshot")
async def kto_region_snapshot(payload: KtoRegionSnapshotRequest):
    """Fetch the verified KTO indicators required for one municipality/month in parallel."""
    try:
        visitor_raw, stay, demand_spend, visitor_diversity, spend_diversity, international_diversity = await asyncio.gather(
            fetch_kto_regional_visitors("local", payload.start_ymd, payload.end_ymd),
            area_metric("demand_intensity", "stay", payload),
            area_metric("demand_intensity", "spend", payload),
            area_metric("tourism_diversity", "visitor", payload),
            area_metric("tourism_diversity", "spend", payload),
            area_metric("tourism_diversity", "international", payload),
        )
        visitor_data = normalize_kto_xml(visitor_raw)
        if isinstance(visitor_data, dict) and isinstance(visitor_data.get("items"), list):
            visitor_data = {**visitor_data, "items": [
                item for item in visitor_data["items"] if item.get("signguCode") == payload.area_cd
            ]}
        return {
            "area_cd": payload.area_cd,
            "base_ym": payload.base_ym,
            "sources": {
                "regional_visitors": visitor_data,
                "demand_intensity_stay": stay,
                "demand_intensity_spend": demand_spend,
                "tourism_diversity_visitor": visitor_diversity,
                "tourism_diversity_spend": spend_diversity,
                "tourism_diversity_international": international_diversity,
            },
        }
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"KTO region-snapshot request failed: {error}")

@app.post("/v1/analysis/live-visitor")
async def live_visitor_analysis(payload: LiveVisitorRequest):
    """Live KTO visitor analysis, explicitly separated from incomplete R-GAP inputs."""
    try:
        metric_payload = KtoAreaMetricRequest(area_cd=payload.area_cd, base_ym=payload.base_ymd[:6])
        raw, stay, spend, visitor_diversity, spend_diversity, international_diversity = await asyncio.gather(
            fetch_kto_regional_visitors("local", payload.base_ymd, payload.base_ymd, 1, 1000),
            area_metric("demand_intensity", "stay", metric_payload),
            area_metric("demand_intensity", "spend", metric_payload),
            area_metric("tourism_diversity", "visitor", metric_payload),
            area_metric("tourism_diversity", "spend", metric_payload),
            area_metric("tourism_diversity", "international", metric_payload),
        )
        snapshot = build_live_visitor_snapshot(raw, payload.area_cd, payload.base_ymd)

        def first_metric(response: object, value_key: str) -> float | None:
            if not isinstance(response, dict): return None
            body = response.get("response", {}).get("body", {})
            items = body.get("items") if isinstance(body, dict) else None
            item = items.get("item") if isinstance(items, dict) else None
            records = item if isinstance(item, list) else ([item] if isinstance(item, dict) else [])
            target_codes = {"52111", "52113"} if payload.area_cd == "52110" else {payload.area_cd}
            values = []
            for record in records:
                if record.get("signguCd") not in target_codes or record.get(value_key) is None: continue
                try: values.append(float(record[value_key]))
                except (TypeError, ValueError): continue
            return round(sum(values) / len(values), 2) if values else None

        snapshot["observed_indices"] = {
            "base_ym": payload.base_ymd[:6],
            "aggregation": "전주시 2개 구 단순평균" if payload.area_cd == "52110" else "해당 시군구",
            "stay_intensity": first_metric(stay, "tarSjrnDsIxVal"),
            "spend_intensity": first_metric(spend, "tarExpDsIxVal"),
            "visitor_diversity": first_metric(visitor_diversity, "touDivIxVal"),
            "spend_diversity": first_metric(spend_diversity, "expDivIxVal"),
            "international_diversity": first_metric(international_diversity, "intlDivIxVal"),
        }
        available = sum(value is not None for key, value in snapshot["observed_indices"].items() if key != "base_ym")
        snapshot["analysis"] = {
            **snapshot["analysis"],
            "status": "partial" if available else "visitor_only",
            "message": f"방문자와 관광수요·다양성 지표 {available}종을 실시간 반영했습니다. 공간분산·계절성·프론티어 모형이 갖춰지면 TCEI와 R-GAP을 산출합니다.",
            "missing_inputs": ["관광지 집중도", "월별 계절성", "숙박공급·접근성", "75분위 프론티어"],
        }
        return snapshot
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"KTO live visitor analysis failed: {error}")

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
