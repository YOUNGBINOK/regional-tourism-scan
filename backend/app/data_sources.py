"""Provider boundary for KTO Data Lab and public-data ingestion.

Endpoint paths stay configurable because the approved service/API product can
differ by Data Lab account and competition scope.
"""
from dataclasses import dataclass
from copy import deepcopy
from urllib.parse import urljoin, unquote
from xml.etree import ElementTree
import httpx
from .settings import get_settings

KTO_SERVICE_CATALOG = {
    "regional_visitors": {"service": "DataLabService", "endpoints": {"metro": "metcoRegnVisitrDDList", "local": "locgoRegnVisitrDDList"}, "coverage": ["외지인·현지인·외국인 일별 방문자"], "integration_status": "verified"},
    "demand_intensity": {"service": "AreaTarDemDsService", "endpoints": {"stay": "areaTarSjrnDsList", "spend": "areaTarExpDsList"}, "coverage": ["숙박 비중·숙박일수별 방문자", "외지인 소비액·소비 비중·방문량 대비 소비"], "integration_status": "verified"},
    "tourism_diversity": {"service": "AreaTarDivService", "endpoints": {"visitor": "areaTouDivList", "spend": "areaExpDivList", "international": "areaIntlDivList"}, "coverage": ["연령별 방문객", "연령별 소비", "외국인 소비·국적 다양성"], "integration_status": "verified"},
    "attraction_concentration": {"service": "TatsCnctrRateService", "endpoints": {}, "coverage": ["관광지 30일 집중률 예측"], "integration_status": "awaiting_operation_name", "note": "승인된 동일 키를 사용합니다. 활용신청 상세의 오퍼레이션명만 환경변수에 입력하면 호출됩니다."},
    "tourism_resource_demand": {"service": "AreaTarResDemService", "endpoints": {}, "coverage": ["SNS·카드·내비 기반 관광 자원 수요"], "integration_status": "awaiting_operation_name", "note": "승인된 동일 키를 사용합니다. 활용신청 상세의 오퍼레이션명만 환경변수에 입력하면 호출됩니다."},
}

def kto_catalog_with_readiness() -> dict[str, object]:
    """Return safe, deployment-ready configuration state without any key value."""
    catalog = deepcopy(KTO_SERVICE_CATALOG)
    s = get_settings()
    configured = {
        "attraction_concentration": s.kto_attraction_concentration_endpoint,
        "tourism_resource_demand": s.kto_tourism_resource_demand_endpoint,
    }
    for dataset, endpoint in configured.items():
        if endpoint:
            catalog[dataset]["endpoints"] = {"default": endpoint}
            catalog[dataset]["integration_status"] = "configured"
    return catalog

@dataclass
class ProviderStatus:
    name: str
    configured: bool
    base_url_configured: bool

def provider_statuses() -> list[ProviderStatus]:
    s = get_settings()
    return [
        ProviderStatus("kto_tourism_datalab", bool(s.kto_tourism_datalab_api_key), bool(s.kto_tourism_datalab_base_url)),
        # KTO products are issued through Data.go.kr, so the KTO service key is
        # also the safe default for the generic Data.go.kr adapter.
        ProviderStatus("public_data_portal", bool(s.public_data_portal_api_key or s.kto_tourism_datalab_api_key), bool(s.public_data_portal_base_url)),
        ProviderStatus("local_finance365", bool(s.local_finance365_api_key), bool(s.local_finance365_base_url)),
        ProviderStatus("kosis", bool(s.kosis_api_key), bool(s.kosis_base_url)),
    ]

def provider_headers(provider: str) -> dict[str, str]:
    s = get_settings()
    key = {"kto_tourism_datalab": s.kto_tourism_datalab_api_key,
           "public_data_portal": s.public_data_portal_api_key or s.kto_tourism_datalab_api_key,
           "local_finance365": s.local_finance365_api_key}.get(provider)
    if not key: raise ValueError(f"{provider} API key is not configured")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

def _provider_config(provider: str) -> tuple[str, str, str]:
    s = get_settings()
    configs = {
        "kto_tourism_datalab": (s.kto_tourism_datalab_base_url, s.kto_tourism_datalab_api_key, s.kto_tourism_datalab_api_key_param),
        "public_data_portal": (s.public_data_portal_base_url, s.public_data_portal_api_key or s.kto_tourism_datalab_api_key, s.public_data_portal_api_key_param),
        "local_finance365": (s.local_finance365_base_url, s.local_finance365_api_key, s.local_finance365_api_key_param),
    }
    if provider not in configs: raise ValueError("Unknown provider")
    base_url, api_key, api_key_param = configs[provider]
    if not base_url or not api_key: raise ValueError(f"{provider} base URL or API key is not configured")
    return base_url.rstrip("/") + "/", api_key, api_key_param

async def fetch_provider_json(provider: str, endpoint: str, params: dict[str, str]) -> object:
    """Fetch only paths relative to the approved provider base URL; keys stay server-side."""
    if endpoint.startswith(("http://", "https://")) or ".." in endpoint:
        raise ValueError("Endpoint must be a relative provider path")
    base_url, api_key, api_key_param = _provider_config(provider)
    # Public Data Portal shows the supplied key as "URL Encode". Decode once
    # before httpx creates the query string, preventing % from becoming %25.
    query = {**params, api_key_param: unquote(api_key)}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(urljoin(base_url, endpoint.lstrip("/")), params=query, headers={"Accept": "application/json"})
        if response.is_error:
            raise RuntimeError(f"{provider} returned HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            return response.json()
        # The gateway supports JSON + XML. Preserve XML rather than treating a
        # valid XML response as a JSON decoding error; normalization follows in ETL.
        return {"format": "xml", "data": response.text}

async def fetch_kto_regional_visitors(scope: str, start_ymd: str, end_ymd: str,
                                      page_no: int = 1, num_rows: int = 1000) -> object:
    endpoints = {"metro": "metcoRegnVisitrDDList", "local": "locgoRegnVisitrDDList"}
    if scope not in endpoints: raise ValueError("scope must be 'metro' or 'local'")
    return await fetch_provider_json("kto_tourism_datalab", endpoints[scope], {
        "numOfRows": str(num_rows), "pageNo": str(page_no), "MobileOS": "ETC",
        "MobileApp": "RGAP", "startYmd": start_ymd, "endYmd": end_ymd,
    })

async def fetch_kto_catalog_service(dataset: str, metric: str, params: dict[str, str]) -> object:
    """Fetch a documented KTO service with the existing Data.go.kr key."""
    spec = KTO_SERVICE_CATALOG.get(dataset)
    if not spec or metric not in spec["endpoints"]: raise ValueError("Unknown or not-yet-documented KTO dataset metric")
    return await fetch_kto_catalog_service_by_path(spec["service"], spec["endpoints"][metric], params)

async def fetch_kto_configured_service(dataset: str, params: dict[str, str]) -> object:
    """Call a KTO product whose operation name was stored in server settings."""
    spec = KTO_SERVICE_CATALOG.get(dataset)
    if dataset not in ("attraction_concentration", "tourism_resource_demand") or not spec:
        raise ValueError("Unsupported KTO configured dataset")
    s = get_settings()
    endpoint = (s.kto_attraction_concentration_endpoint if dataset == "attraction_concentration"
                else s.kto_tourism_resource_demand_endpoint)
    if not endpoint:
        raise ValueError("KTO operation name is not configured yet")
    if "/" in endpoint or ".." in endpoint:
        raise ValueError("KTO operation name must be a single path segment")
    return await fetch_kto_catalog_service_by_path(spec["service"], endpoint, params)

async def fetch_kto_catalog_service_by_path(service: str, endpoint: str, params: dict[str, str]) -> object:
    """Fetch one documented KTO operation while keeping the credential private."""
    s = get_settings()
    if not s.kto_tourism_datalab_api_key:
        raise ValueError("KTO API key is not configured")
    query = {"MobileOS": "ETC", "MobileApp": "RGAP", "numOfRows": "1000", "pageNo": "1", **params,
             "serviceKey": unquote(s.kto_tourism_datalab_api_key)}
    url = f"https://apis.data.go.kr/B551011/{service}/{endpoint}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=query, headers={"Accept": "application/json"})
        if response.is_error:
            raise RuntimeError(f"KTO {service}/{endpoint} returned HTTP {response.status_code}")
        if "json" in response.headers.get("content-type", "").lower():
            return response.json()
        return {"format": "xml", "data": response.text}

def normalize_kto_xml(payload: object) -> object:
    """Convert standard Public Data Portal XML envelopes into API-friendly JSON."""
    if not isinstance(payload, dict) or payload.get("format") != "xml": return payload
    root = ElementTree.fromstring(str(payload["data"]))
    def value(path: str):
        node = root.find(path)
        return node.text if node is not None else None
    items = []
    for item in root.findall(".//item"):
        items.append({child.tag: child.text for child in item})
    return {"result_code": value(".//header/resultCode"), "result_message": value(".//header/resultMsg"),
            "page_no": value(".//body/pageNo"), "num_of_rows": value(".//body/numOfRows"),
            "total_count": value(".//body/totalCount"), "items": items}

def _json_envelope_items(payload: object) -> list[dict]:
    """Public Data Portal JSON envelopes keep the nested response.body.items.item
    shape (unlike XML, which normalize_kto_xml already flattens). Extract a flat
    list either way so callers don't need to know which transport was used."""
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if not isinstance(payload, dict): return []
    body = payload.get("response", {}).get("body", {}) if isinstance(payload.get("response"), dict) else {}
    items = body.get("items") if isinstance(body, dict) else None
    item = items.get("item") if isinstance(items, dict) else None
    if isinstance(item, list): return item
    if isinstance(item, dict): return [item]
    return []

async def fetch_attraction_concentration(area_cd: str) -> object | None:
    """30-day-ahead per-attraction concentration forecast (TatsCnctrRateService).

    Returns None instead of raising when the operation name isn't configured
    for this deployment yet, so callers can degrade to "pending" gracefully.
    """
    if not get_settings().kto_attraction_concentration_endpoint:
        return None
    raw = await fetch_kto_configured_service("attraction_concentration", {
        "areaCd": area_cd[:2], "signguCd": area_cd, "numOfRows": "1000", "pageNo": "1", "_type": "json",
    })
    return normalize_kto_xml(raw)

def compute_spatial_dispersion(data: object) -> dict[str, object] | None:
    """Turn per-attraction concentration forecasts into a region-level dispersion index.

    For each forecast date, treat each attraction's cnctrRate as a share of that
    day's total and compute the Herfindahl index (sum of squared shares): higher
    means visits cluster on fewer attractions (single-point concentration),
    lower means visits spread across many. dispersion_index = 100 - avg HHI*100,
    so a higher value means more even spatial spread, matching the "higher is
    better" convention used by the other observed indices.
    """
    items = _json_envelope_items(data)
    if not items: return None
    by_date: dict[str, list[float]] = {}
    for item in items:
        ymd = item.get("baseYmd")
        try: rate = float(item.get("cnctrRate") or 0)
        except (TypeError, ValueError): continue
        if ymd: by_date.setdefault(ymd, []).append(rate)
    hhis = []
    for rates in by_date.values():
        total = sum(rates)
        if total <= 0 or len(rates) < 2: continue
        hhis.append(sum((rate / total) ** 2 for rate in rates))
    if not hhis: return None
    concentration_index = round(100 * sum(hhis) / len(hhis), 1)
    return {
        "attractions_tracked": len({item.get("tAtsNm") for item in items if item.get("tAtsNm")}),
        "days_observed": len(by_date),
        "concentration_index": concentration_index,
        "dispersion_index": round(100 - concentration_index, 1),
    }

async def fetch_visitor_window(start_ymd: str, end_ymd: str) -> list[dict]:
    """Fetch every daily local-visitor record in [start_ymd, end_ymd], paginating as needed."""
    page_no, num_rows, items = 1, 1000, []
    while True:
        raw = await fetch_kto_regional_visitors("local", start_ymd, end_ymd, page_no, num_rows)
        data = normalize_kto_xml(raw)
        if not isinstance(data, dict) or data.get("result_code") != "0000":
            raise ValueError("KTO visitor window response could not be normalized")
        page_items = data.get("items", [])
        items.extend(page_items)
        total = int(data.get("total_count") or 0)
        if not page_items or page_no * num_rows >= total: break
        page_no += 1
    return items

def compute_visitor_stability(items: list[dict], area_cds: list[str]) -> dict[str, dict[str, object]]:
    """Day-to-day outside-visitor volatility within the fetched window, per area.

    This is a real, data-derived proxy for §4.1's "계절/시간 안정성" axis, scoped
    to the fetched day window (not a full annual seasonal cycle — that needs a
    12-month pipeline that isn't wired up yet). stability_index = 100*(1-CV),
    clamped to 0..100, so higher means steadier day-to-day demand.
    """
    by_area_day: dict[str, dict[str, float]] = {code: {} for code in area_cds}
    for item in items:
        code = item.get("signguCode")
        if code not in by_area_day: continue
        if not str(item.get("touDivNm", "")).startswith("외지인"): continue
        ymd = item.get("baseYmd")
        try: value = float(str(item.get("touNum", "0")).replace(",", ""))
        except ValueError: continue
        if ymd: by_area_day[code][ymd] = by_area_day[code].get(ymd, 0.0) + value
    result: dict[str, dict[str, object]] = {}
    for code, daily in by_area_day.items():
        values = list(daily.values())
        if len(values) < 3:
            result[code] = {"days_observed": len(values), "stability_index": None}
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        cv = (variance ** 0.5) / mean if mean else None
        stability_index = round(max(0.0, min(100.0, 100 * (1 - cv))), 1) if cv is not None else None
        result[code] = {"days_observed": len(values), "stability_index": stability_index}
    return result

def build_live_visitor_snapshot(payload: object, area_cd: str, base_ymd: str) -> dict[str, object]:
    """Turn the KTO GW response into a traceable, non-modelled live snapshot.

    TCEI and R-GAP are intentionally not inferred here: those measures need
    stay, spend, dispersion and seasonal inputs. Returning null prevents a
    visitor-only feed from being presented as a completed R-GAP diagnosis.
    """
    data = normalize_kto_xml(payload)
    if not isinstance(data, dict) or data.get("result_code") != "0000":
        raise ValueError("KTO visitor response could not be normalized")
    by_area: dict[str, dict[str, object]] = {}
    for item in data.get("items", []):
        code = item.get("signguCode")
        if not code:
            continue
        try:
            value = float(str(item.get("touNum", "0")).replace(",", ""))
        except ValueError:
            value = 0.0
        entry = by_area.setdefault(code, {
            "area_cd": code, "area_name": item.get("signguNm", code),
            "resident_visitors": 0.0, "outside_visitors": 0.0, "foreign_visitors": 0.0,
        })
        category = item.get("touDivNm", "")
        # The KTO GW labels include category codes, e.g. "외지인(b)".
        if category.startswith("외지인"):
            entry["outside_visitors"] = float(entry["outside_visitors"]) + value
        elif category.startswith("외국인"):
            entry["foreign_visitors"] = float(entry["foreign_visitors"]) + value
        elif category.startswith("현지인"):
            entry["resident_visitors"] = float(entry["resident_visitors"]) + value
    target = by_area.get(area_cd)
    if not target:
        raise ValueError("Selected municipality is not present in this KTO daily response")
    external_values = sorted(float(entry["outside_visitors"]) for entry in by_area.values())
    current = float(target["outside_visitors"])
    percentile = round(100 * sum(value <= current for value in external_values) / len(external_values), 1) if external_values else 0.0
    total = sum(float(target[key]) for key in ("resident_visitors", "outside_visitors", "foreign_visitors"))
    return {
        "source": "한국관광공사 빅데이터 지역별 방문자수_GW",
        "source_status": "live",
        "base_ymd": base_ymd,
        "area": target,
        "national_comparison": {"municipality_count": len(by_area), "outside_visitor_percentile": percentile},
        "visitor_mix": {
            "outside_share": round(100 * current / total, 1) if total else 0.0,
            "foreign_share": round(100 * float(target["foreign_visitors"]) / total, 1) if total else 0.0,
        },
        "analysis": {
            "tcei": None, "r_gap": None,
            "status": "partial",
            "message": "실시간 방문자 지표는 반영됐습니다. 체류·소비·공간·시간 지표가 적재되면 TCEI와 R-GAP을 산출합니다.",
            "missing_inputs": ["체류·숙박", "관광소비", "공간분산", "월별 계절성"],
        },
    }
