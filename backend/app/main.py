import asyncio
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from .models import TceiInput, BudgetRequest, calculate_tcei, calculate_r_gap, allocate_budget
from .data_sources import (provider_statuses, fetch_provider_json, fetch_kto_regional_visitors,
                           fetch_kto_catalog_service, fetch_kto_configured_service,
                           normalize_kto_xml, kto_catalog_with_readiness, build_live_visitor_snapshot,
                           fetch_attraction_concentration, summarize_attraction_concentration,
                           fetch_municipal_hub_attractions, compute_hub_spatial_spread,
                           fetch_visitor_window, compute_visitor_stability,
                           fetch_national_visitor_ranking_window,
                           is_independent_municipality,
                           build_peer_group, build_pg_categories, PG_CATEGORY_LABELS,
                           _percentile_rank, _quantile,
                           fetch_kosis_statistics, fetch_mois_tourism_business,
                           summarize_mois_tourism_business, mois_tourism_business_regions,
                           fetch_mois_city_business_summary, _cached)
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

class VisitorStabilityRequest(BaseModel):
    area_cds: list[str] = Field(min_length=1, max_length=12)
    base_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260701"])
    window_days: int = Field(default=7, ge=3, le=14)

class NationalPeersRequest(BaseModel):
    area_cd: str = Field(pattern=r"^\d{5}$", examples=["47130"])
    base_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260701"])
    peer_count: int = Field(default=4, ge=1, le=12)
    window_days: int = Field(default=7, ge=3, le=14)

class NationalRankingRequest(BaseModel):
    base_ymd: str = Field(pattern=r"^\d{8}$", examples=["20260701"])
    window_days: int = Field(default=7, ge=3, le=14)

@app.get("/health")
def health(): return {"status": "ok", "service": "R-GAP API"}

@app.get("/v1/data-sources/status")
def data_source_status():
    """Safe status only: API credentials are never returned to the client."""
    return {"sources": [status.__dict__ for status in provider_statuses()]}

@app.get("/v1/data-sources/kosis/{dataset}")
async def kosis_dataset(dataset: str):
    """Safe KOSIS preview for the configured structural Peer variables."""
    from .settings import get_settings
    query = {"population": get_settings().kosis_population_query,
             "area": get_settings().kosis_area_query}.get(dataset)
    if query is None:
        raise HTTPException(status_code=422, detail="dataset must be population or area")
    try:
        return await fetch_kosis_statistics(query)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"KOSIS {dataset} request failed: {error}")

@app.get("/v1/data-sources/mois/tourism-business/raw/{operation}")
async def mois_tourism_business(operation: str, open_authority_code: str,
                                base_date: str | None = None, page_no: int = 1,
                                num_rows: int = 100):
    """MOIS /info or /history preview, with source-safe lodging-supply summary."""
    try:
        if page_no < 1 or not 1 <= num_rows <= 100:
            raise ValueError("page_no must be >= 1 and num_rows must be 1..100")
        return summarize_mois_tourism_business(await fetch_mois_tourism_business(
            operation, open_authority_code, base_date, page_no, num_rows))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"MOIS tourism-business request failed: {error}")


@app.get("/v1/data-sources/mois/tourism-business/regions")
def mois_tourism_business_region_list():
    """Safe region choices for the UI. Provider opening-authority codes stay private."""
    return {"regions": mois_tourism_business_regions()}


@app.get("/v1/data-sources/mois/tourism-business/region/{region_id}/{operation}")
async def mois_tourism_business_for_region(region_id: str, operation: str, base_date: str | None = None):
    """User-facing lookup by municipality rather than OPN_ATMY_GRP_CD.

    OPN_ATMY_GRP_CD only resolves to a *province*-level reporting group (see
    MOIS_TOURISM_BUSINESS_REGIONS), so fetch_mois_city_business_summary pages
    through the whole group and filters to the target city's own address —
    otherwise this would silently show a neighboring city's businesses under
    the selected city's label. No page_no/num_rows here anymore: the city
    filter has to run over every page of the group, so partial paging from
    the caller wouldn't produce a meaningful count.
    """
    try:
        result = await fetch_mois_city_business_summary(region_id, operation, base_date)
        result["region"] = next((region for region in mois_tourism_business_regions()
                                 if region["id"] == region_id), None)
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"MOIS tourism-business request failed: {error}")

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

async def area_metric(dataset: str, metric: str, payload: KtoAreaMetricRequest,
                      index_filter: dict[str, str] | None = None):
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
        **(index_filter if index_filter is not None else index_filters.get((dataset, metric), {})),
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

def _first_metric(response: object, value_key: str, area_cd: str) -> float | None:
    if not isinstance(response, dict): return None
    body = response.get("response", {}).get("body", {})
    items = body.get("items") if isinstance(body, dict) else None
    item = items.get("item") if isinstance(items, dict) else None
    records = item if isinstance(item, list) else ([item] if isinstance(item, dict) else [])
    target_codes = {"52111", "52113"} if area_cd == "52110" else {area_cd}
    values = []
    for record in records:
        if record.get("signguCd") not in target_codes or record.get(value_key) is None: continue
        try: values.append(float(record[value_key]))
        except (TypeError, ValueError): continue
    return round(sum(values) / len(values), 2) if values else None

async def _safe_attraction_concentration(area_cd: str):
    """Concentration forecast is best-effort: missing/unapproved config must not fail the whole snapshot."""
    try: return await fetch_attraction_concentration(area_cd)
    except Exception: return None

async def _safe_hub_attractions(area_cd: str, base_ym: str):
    """Approved hub-attraction feed is best-effort so older empty months do not break the snapshot."""
    try: return await fetch_municipal_hub_attractions(area_cd, base_ym)
    except Exception: return None

async def _national_position(area_cd: str, base_ymd: str, window_days: int) -> tuple[list, dict | None]:
    """The one place national demand percentile is computed, so every screen that
    states a region's national position states the *same* number.

    Before this, /v1/analysis/live-visitor computed its own same-day percentile
    over every raw KTO code (including 일반구, ~264-270 municipalities) while
    /v1/analysis/national-peers computed a separate 7-day-windowed percentile
    over independent municipalities only (~229) — a live diagnosis screen could
    show both side by side (e.g. "백분위 73.5%, 264개 지역" next to "백분위 76%,
    229곳") for the same region, which undermines both once anyone compares them.

    Returns (independent_ranking, position) — the filtered ranking so callers
    that also need Peer Group construction don't have to re-filter it, and
    position=None when the area isn't present in this window's feed.
    """
    ranking = await fetch_national_visitor_ranking_window(base_ymd, window_days)
    independent_ranking = [entry for entry in ranking if is_independent_municipality(str(entry["area_name"]))]
    target = next((entry for entry in independent_ranking if entry["area_cd"] == area_cd), None)
    if target is None:
        return independent_ranking, None
    independent_values = [float(entry["outside_visitors"]) for entry in independent_ranking]
    percentile = _percentile_rank(float(target["outside_visitors"]), independent_values)
    return independent_ranking, {
        "municipality_count": len(independent_ranking),
        "outside_visitor_percentile": percentile,
        "demand_level": "충분" if percentile >= 50 else "부족",
        "window_days": window_days,
        "target": target,
    }

async def _safe_national_position(area_cd: str, base_ymd: str) -> dict | None:
    """National position is best-effort here: a scan failure must not break
    the rest of the live snapshot, it just falls back to the same-day figure
    build_live_visitor_snapshot already computed from the raw payload."""
    try:
        _, position = await _national_position(area_cd, base_ymd, window_days=7)
        return position
    except Exception:
        return None

async def _build_live_visitor_snapshot(payload: LiveVisitorRequest) -> dict:
        metric_payload = KtoAreaMetricRequest(area_cd=payload.area_cd, base_ym=payload.base_ymd[:6])
        (raw, stay, lodging_share, one_night, two_nights, three_plus_nights,
         spend, visitor_diversity, spend_diversity, international_diversity, concentration_raw, hubs_raw,
         national_position) = await asyncio.gather(
            fetch_kto_regional_visitors("local", payload.base_ymd, payload.base_ymd, 1, 1000),
            area_metric("demand_intensity", "stay", metric_payload),
            area_metric("demand_intensity", "stay", metric_payload, {"tarSjrnDsIxCd": "2102"}),
            area_metric("demand_intensity", "stay", metric_payload, {"tarSjrnDsIxCd": "2103"}),
            area_metric("demand_intensity", "stay", metric_payload, {"tarSjrnDsIxCd": "2104"}),
            area_metric("demand_intensity", "stay", metric_payload, {"tarSjrnDsIxCd": "2105"}),
            area_metric("demand_intensity", "spend", metric_payload),
            area_metric("tourism_diversity", "visitor", metric_payload),
            area_metric("tourism_diversity", "spend", metric_payload),
            area_metric("tourism_diversity", "international", metric_payload),
            _safe_attraction_concentration(payload.area_cd),
            _safe_hub_attractions(payload.area_cd, payload.base_ymd[:6]),
            _safe_national_position(payload.area_cd, payload.base_ymd),
        )
        snapshot = build_live_visitor_snapshot(raw, payload.area_cd, payload.base_ymd)
        concentration = summarize_attraction_concentration(concentration_raw)
        hub_spread = compute_hub_spatial_spread(hubs_raw)
        if national_position is not None:
            # Same 7일 평균·기초지자체-only figure §04 shows — see _national_position's
            # docstring for why this used to be two different numbers.
            snapshot["national_comparison"] = {
                "municipality_count": national_position["municipality_count"],
                "outside_visitor_percentile": national_position["outside_visitor_percentile"],
                "window_days": national_position["window_days"],
            }

        snapshot["observed_indices"] = {
            "base_ym": payload.base_ymd[:6],
            "aggregation": "전주시 2개 구 단순평균" if payload.area_cd == "52110" else "해당 시군구",
            "stay_intensity": _first_metric(stay, "tarSjrnDsIxVal", payload.area_cd),
            "lodging_share_index": _first_metric(lodging_share, "tarSjrnDsIxVal", payload.area_cd),
            "one_night_index": _first_metric(one_night, "tarSjrnDsIxVal", payload.area_cd),
            "two_nights_index": _first_metric(two_nights, "tarSjrnDsIxVal", payload.area_cd),
            "three_plus_nights_index": _first_metric(three_plus_nights, "tarSjrnDsIxVal", payload.area_cd),
            "spend_intensity": _first_metric(spend, "tarExpDsIxVal", payload.area_cd),
            "visitor_diversity": _first_metric(visitor_diversity, "touDivIxVal", payload.area_cd),
            "spend_diversity": _first_metric(spend_diversity, "expDivIxVal", payload.area_cd),
            "international_diversity": _first_metric(international_diversity, "intlDivIxVal", payload.area_cd),
            "attraction_crowding_forecast": concentration["mean_crowding_rate"] if concentration else None,
            "spatial_dispersion": hub_spread["spread_km"] if hub_spread else None,
            "spatial_dispersion_detail": hub_spread,
        }
        available = sum(1 for key, value in snapshot["observed_indices"].items()
                        if key not in {"base_ym", "aggregation", "spatial_dispersion_detail"} and value is not None)
        missing_inputs = ["관광지별 실제 방문 점유율(정식 D 산출)", "숙박시설 공급량·객실수", "월별 계절성(연 단위)", "75분위 프론티어"]
        snapshot["analysis"] = {
            **snapshot["analysis"],
            "status": "partial" if available else "visitor_only",
            "message": f"방문자·체류·숙박·소비·다양성·혼잡예측·중심관광지 공간확산 지표 {available}종을 반영했습니다. 실제 방문점유율·연간 계절성·숙박시설 공급·프론티어 모형이 갖춰지면 TCEI와 R-GAP을 산출합니다.",
            "missing_inputs": missing_inputs,
        }
        return snapshot

@app.post("/v1/analysis/live-visitor")
async def live_visitor_analysis(payload: LiveVisitorRequest):
    """Live KTO visitor analysis, explicitly separated from incomplete R-GAP inputs.

    Cached by (area_cd, base_ymd): this fans out 12 KTO calls, so revisiting
    the same diagnosis target on the same day (a click, a date-picker no-op,
    a page refresh) reuses the cached snapshot instead of repeating them.
    """
    try:
        return await _cached(f"live:{payload.area_cd}:{payload.base_ymd}",
                             lambda: _build_live_visitor_snapshot(payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"KTO live visitor analysis failed: {error}")

@app.post("/v1/analysis/visitor-stability")
async def visitor_stability(payload: VisitorStabilityRequest):
    """Day-to-day outside-visitor volatility across a shared window, for every requested area at once.

    One shared fetch covers all areas because the local-visitor feed isn't
    filterable by municipality server-side. This is a short-term stability
    proxy, not the annual seasonality axis required by AGENTS.md §4.1.
    """
    try:
        base = date(int(payload.base_ymd[:4]), int(payload.base_ymd[4:6]), int(payload.base_ymd[6:8]))
        start = base - timedelta(days=payload.window_days - 1)
        items = await fetch_visitor_window(start.strftime("%Y%m%d"), payload.base_ymd)
        return {"window_days": payload.window_days, "start_ymd": start.strftime("%Y%m%d"), "end_ymd": payload.base_ymd,
                "areas": compute_visitor_stability(items, payload.area_cds)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"KTO visitor stability request failed: {error}")

def _median(values: list[float | None]) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean: return None
    mid = len(clean) // 2
    return clean[mid] if len(clean) % 2 else (clean[mid - 1] + clean[mid]) / 2

async def _fetch_peer_axis_snapshot(area_cd: str, base_ym: str) -> dict[str, float | None] | None:
    """Peer snapshot for national peer benchmarking — stay, spend, lodging-share,
    and hub-attraction spatial spread (4 calls; Jeonju's split-district lookup
    costs 2). Returns None for the whole peer on any failure, so one bad peer
    (quota, network, missing data) just drops out of the median instead of
    failing the whole national-peers response.

    Cached by (area_cd, base_ym): the same handful of national top-demand
    regions (e.g. 강남구, 서초구) show up as peers for most diagnosis targets,
    so this avoids re-fetching their axis data on every target switch.
    """
    async def _fetch() -> dict[str, float | None] | None:
        try:
            metric_payload = KtoAreaMetricRequest(area_cd=area_cd, base_ym=base_ym)
            stay, spend, lodging, hubs_raw = await asyncio.gather(
                area_metric("demand_intensity", "stay", metric_payload),
                area_metric("demand_intensity", "spend", metric_payload),
                area_metric("demand_intensity", "stay", metric_payload, {"tarSjrnDsIxCd": "2102"}),
                _safe_hub_attractions(area_cd, base_ym),
            )
            hub_spread = compute_hub_spatial_spread(hubs_raw) if hubs_raw else None
            return {
                "stay_intensity": _first_metric(stay, "tarSjrnDsIxVal", area_cd),
                "spend_intensity": _first_metric(spend, "tarExpDsIxVal", area_cd),
                "lodging_share_index": _first_metric(lodging, "tarSjrnDsIxVal", area_cd),
                "dispersion_spread_km": hub_spread["spread_km"] if hub_spread else None,
            }
        except Exception:
            return None
    return await _cached(f"peeraxis:{area_cd}:{base_ym}", _fetch)

@app.post("/v1/analysis/national-peers")
async def national_peers(payload: NationalPeersRequest):
    """Two separate comparisons, not one blended list (AGENTS.md 원칙 1-5):

    ① 전국 위치 — the target's demand percentile among every 기초지자체
       (시/군/자치구; 일반구 excluded, it isn't an independent municipality).
       Used only to judge whether demand itself is already sufficient.
    ② Peer Group — a *structural-condition* comparison group for diagnosing
       stay/spend/lodging weakness: same 행정유형(시/군/자치구), same
       수도권 여부 for 시/군, then the closest matches by demand *scale*
       (percentile proximity) within that pool. This is deliberately not
       "top-N by national demand" — see build_peer_group()'s docstring.

    Never raises for a scan or peer-fetch failure — degrades to
    available:false so the UI never has to render a bare error here.
    """
    try:
        independent_ranking, position = await _national_position(payload.area_cd, payload.base_ymd, payload.window_days)
    except Exception as error:
        return {"available": False, "reason": str(error), "base_ymd": payload.base_ymd}

    if position is None:
        return {"available": False, "reason": "선택 지역이 최근 방문자 원천에 없습니다.", "base_ymd": payload.base_ymd}
    target = position["target"]
    national_percentile = position["outside_visitor_percentile"]
    demand_level = position["demand_level"]

    # ② Peer Group: 행정유형 + 수도권 여부 + 관광수요·인구·인구밀도 규모 유사도로 구성하고,
    # 그 위에 밀도 기반 PG-1~4(도시 성격)를 추가 정렬 기준으로 얹는다 — 관광수요·숙박
    # 같은 outcome 변수는 그룹 구성에 쓰지 않는다는 원칙은 그대로 지킨다(build_pg_categories 참고).
    base_ym = payload.base_ymd[:6]
    pg_categories = await build_pg_categories(independent_ranking, base_ym)
    group = await build_peer_group(independent_ranking, payload.area_cd, str(target["area_name"]),
                                   payload.peer_count, base_ym, pg_categories)
    peers = group["peers"]
    peer_axes = await asyncio.gather(*[_fetch_peer_axis_snapshot(peer["area_cd"], base_ym) for peer in peers])
    peer_rows = [{
        "area_cd": peer["area_cd"], "area_name": peer["area_name"], "rank": peer["rank"],
        "outside_visitors": peer["outside_visitors"], "percentile": peer["percentile"],
        "population": peer.get("population"), "population_density": peer.get("population_density"),
        "pg_category": peer.get("pg_category"),
        "axes": axes, "fetch_ok": axes is not None,
    } for peer, axes in zip(peers, peer_axes)]

    def _peer_values(key: str) -> list[float]:
        return [row["axes"][key] for row in peer_rows if row["axes"] and row["axes"][key] is not None]

    axis_keys = ["stay_intensity", "spend_intensity", "lodging_share_index", "dispersion_spread_km"]
    return {
        "available": True,
        "base_ymd": payload.base_ymd,
        "window_days": payload.window_days,
        "national": {
            "municipality_count": len(independent_ranking),
            "target_percentile": national_percentile,
            "demand_level": demand_level,
        },
        "target": target,
        "peer_group": {
            "admin_type": group["admin_type"],
            "capital_region": group["capital_region"],
            "relaxed": group["relaxed"],
            "criteria_note": group["criteria_note"],
            "pg_category": group["pg_category"],
            "pg_category_label": PG_CATEGORY_LABELS.get(group["pg_category"]) if group["pg_category"] else None,
            "count": len(peer_rows),
            "peers": peer_rows,
            "medians": {key: _median(_peer_values(key)) for key in axis_keys},
            "top_quartile": {key: _quantile(_peer_values(key), 0.75) for key in axis_keys},
            # Peer Group's own lower quartile per axis — the distribution-based
            # floor "취약" judgments are measured against, instead of a fixed
            # ±p constant that has no relationship to how spread out this
            # particular peer group actually is (AGENTS.md 원칙 6 후속 조치).
            "bottom_quartile": {key: _quantile(_peer_values(key), 0.25) for key in axis_keys},
            # How many peers actually backed each axis's bottom_quartile.
            # With only 3-4 points, a linear-interpolation 25th percentile
            # is really just "the 2nd-lowest value" — a target barely below
            # it looks "weak" far more often than the label should mean.
            # Discovered by running scripts/national_diagnosis_snapshot.py
            # across 100 real municipalities: ~56% came back 숨은취약형 with
            # this sample size, an implausible rate that only a live batch
            # run — not code review alone — surfaced. The frontend must
            # gate weak-axis judgments on this count instead of trusting
            # any bottom_quartile value that happens to be non-null.
            "sample_size": {key: len(_peer_values(key)) for key in axis_keys},
            "target_population": group.get("target_population"),
            "target_population_density": group.get("target_population_density"),
        },
        "peers_failed": sum(1 for row in peer_rows if not row["fetch_ok"]),
    }

@app.post("/v1/analysis/national-ranking")
async def national_ranking(payload: NationalRankingRequest):
    """Multi-day-average national outside-visitor ranking (no per-peer axis calls).

    Used to populate every currently-reporting municipality as a selectable
    diagnosis target — e.g. clickable map pins — beyond the 4 curated
    cities. Never raises: a scan failure degrades to available:false with
    HTTP 200.
    """
    try:
        ranking = await fetch_national_visitor_ranking_window(payload.base_ymd, payload.window_days)
        return {"available": True, "base_ymd": payload.base_ymd, "window_days": payload.window_days, "regions": ranking}
    except Exception as error:
        return {"available": False, "reason": str(error), "base_ymd": payload.base_ymd}

@app.post("/v1/metrics/tcei")
def tcei(payload: TceiInput):
    """Reference implementation of AGENTS.md §4.6's TCEI formula, given
    already-computed component scores. Not called by the live diagnosis
    pipeline: TCEI needs a real visit-share D, annual seasonality, and a
    spend residual, none of which are backed by verified data sources yet
    (see build_live_visitor_snapshot's missing_inputs). Exposed so the
    scoring formula itself is inspectable and testable independent of that
    data-availability gap — treat its output as a worked example, not a
    live regional score."""
    return calculate_tcei(payload)

@app.post("/v1/regions/{region_code}/r-gap")
def r_gap(region_code: str, actual_tcei: float, frontier_tcei: float,
          actual_pg_category: str | None = None, frontier_pg_category: str | None = None):
    """Reference implementation of AGENTS.md §4.6's R-GAP formula given two
    already-computed TCEI values. Not called by the live diagnosis pipeline
    for the same reason as /v1/metrics/tcei — see its docstring. The optional
    pg_category args enforce calculate_r_gap's same-category constraint when
    supplied; see that function's docstring for why cross-category framing
    would misrepresent a region's actual gap."""
    try:
        gap = calculate_r_gap(actual_tcei, frontier_tcei, actual_pg_category, frontier_pg_category)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return {"region_code": region_code, "actual_tcei": actual_tcei, "frontier_tcei": frontier_tcei, "r_gap": gap}

@app.post("/v1/budget/portfolio")
def portfolio(payload: BudgetRequest):
    try: return allocate_budget(payload)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error))
