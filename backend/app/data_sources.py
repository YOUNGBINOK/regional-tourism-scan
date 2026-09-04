"""Provider boundary for KTO Data Lab and public-data ingestion.

Endpoint paths stay configurable because the approved service/API product can
differ by Data Lab account and competition scope.
"""
from dataclasses import dataclass
from copy import deepcopy
from datetime import date, timedelta
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, unquote, urlsplit
from xml.etree import ElementTree
from math import asin, cos, radians, sin, sqrt
import httpx
from .settings import get_settings

# KTO's daily per-product call quota (commonly 1,000/day) is the tightest
# constraint on this app, not latency, and every value here is keyed by a
# same-day input (a base_ymd or a specific area+month) that cannot change
# within that day. Caching those results — even for a modest TTL — turns
# repeat requests (switching between diagnosis targets that share the same
# national top-demand peers, re-fetching the same 7-day stability window,
# revisiting a region) from real KTO calls into free cache hits. This only
# helps within one warm process (a dev server, or a warm serverless
# instance during a burst of traffic) — it resets on a cold start — but
# that is exactly the pattern that was burning through quota: many requests
# in quick succession re-deriving the same national scan.
_CACHE_TTL_SECONDS = 1800.0
_response_cache: dict[str, tuple[float, object]] = {}
# A page load can ask for the national ranking, PG categories and a selected
# region's peer snapshot at nearly the same time. On a cold serverless
# instance those requests used to miss the cache together and fan out into
# duplicate KTO calls. Keep one producer per cache key so every concurrent
# caller awaits the same result instead of spending the same API quota again.
_inflight_cache: dict[str, asyncio.Task] = {}
# The KTO gateway also applies a short-window throttle. The peer calculation
# needs several independent KTO products, so cap in-process request fan-out;
# retries alone cannot prevent the initial burst that causes HTTP 429.
_kto_request_semaphore = asyncio.Semaphore(3)

async def _cached(key: str, factory, ttl: float = _CACHE_TTL_SECONDS) -> object:
    entry = _response_cache.get(key)
    if entry is not None and time.monotonic() - entry[0] <= ttl:
        return entry[1]
    task = _inflight_cache.get(key)
    if task is None:
        task = asyncio.create_task(factory())
        _inflight_cache[key] = task
    try:
        value = await task
        _response_cache[key] = (time.monotonic(), value)
        return value
    finally:
        # Do not retain failures or completed tasks. A later request can
        # retry a transient provider error, while successful calls use cache.
        if _inflight_cache.get(key) is task and task.done():
            _inflight_cache.pop(key, None)

KTO_SERVICE_CATALOG = {
    "regional_visitors": {"service": "DataLabService", "endpoints": {"metro": "metcoRegnVisitrDDList", "local": "locgoRegnVisitrDDList"}, "coverage": ["외지인·현지인·외국인 일별 방문자"], "integration_status": "verified"},
    "demand_intensity": {"service": "AreaTarDemDsService", "endpoints": {"stay": "areaTarSjrnDsList", "spend": "areaTarExpDsList"}, "coverage": ["숙박 비중·숙박일수별 방문자", "외지인 소비액·소비 비중·방문량 대비 소비"], "integration_status": "verified"},
    "tourism_diversity": {"service": "AreaTarDivService", "endpoints": {"visitor": "areaTouDivList", "spend": "areaExpDivList", "international": "areaIntlDivList"}, "coverage": ["연령별 방문객", "연령별 소비", "외국인 소비·국적 다양성"], "integration_status": "verified"},
    "attraction_concentration": {"service": "TatsCnctrRateService", "endpoints": {}, "coverage": ["관광지 30일 집중률 예측"], "integration_status": "awaiting_operation_name", "note": "승인된 동일 키를 사용합니다. 활용신청 상세의 오퍼레이션명만 환경변수에 입력하면 호출됩니다."},
    "municipal_hub_attractions": {"service": "LocgoHubTarService1", "endpoints": {"default": "areaBasedList1"}, "coverage": ["내비게이션 연계 중심 관광지 순위·좌표"], "integration_status": "verified"},
    "tourism_resource_demand": {"service": "AreaTarResDemService", "endpoints": {}, "coverage": ["SNS·카드·내비 기반 관광 자원 수요"], "integration_status": "awaiting_operation_name", "note": "승인된 동일 키를 사용합니다. 활용신청 상세의 오퍼레이션명만 환경변수에 입력하면 호출됩니다."},
}

# The daily visitor feed reports these municipalities with a city-level code,
# while KTO's analytical products return their constituent districts only.
# Keep the mapping next to the provider adapter so every KTO product (indices
# and navigation hubs) expands the same source codes.
SPLIT_CITY_DISTRICT_CODES: dict[str, set[str]] = {
    "41110": {"41111", "41113", "41115", "41117"},  # 수원시
    "41130": {"41131", "41133", "41135"},              # 성남시
    "41170": {"41171", "41173"},                       # 안양시
    "41270": {"41271", "41273"},                       # 안산시
    "41280": {"41281", "41285", "41287"},              # 고양시
    "41460": {"41461", "41463", "41465"},              # 용인시
    "43110": {"43111", "43112", "43113", "43114"},  # 청주시
    "44130": {"44131", "44133"},                       # 천안시
    "47110": {"47111", "47113"},                       # 포항시
    "48120": {"48121", "48123", "48125", "48127", "48129"},  # 창원시
    "52110": {"52111", "52113"},                       # 전주시
}


def metric_source_codes(area_cd: str) -> set[str]:
    """KTO analytics source codes that represent one selected municipality."""
    return SPLIT_CITY_DISTRICT_CODES.get(area_cd, {area_cd})

# 행정안전부 지방행정 인허가 데이터의 개방자치단체코드. 화면에는 지역명만
# 노출하고 서버에서 코드로 변환한다. 현재 KTO 실시간 진단에 제공 중인 4개
# 지역부터 시작하며, 전국 적재 단계에서 공식 코드표 전체로 확장한다.
MOIS_TOURISM_BUSINESS_REGIONS = {
    # open_authority_code(OPN_ATMY_GRP_CD) is a *province-level* reporting-group
    # code, not a per-시군구 code — the four values below were verified by
    # pulling the full live feed (2,644 records) and reading which code every
    # record whose address contains each city name actually carries. The
    # previous values ("5050000" etc.) were unverified guesses that always
    # returned zero — every "0건" the UI ever showed for these regions was a
    # code-mapping failure, not a real zero (see fetch_mois_tourism_business_for_region()
    # for the client-side city-name filter this makes necessary).
    "47130": {"name": "경주시", "province": "경상북도", "open_authority_code": "6470000"},
    "51150": {"name": "강릉시", "province": "강원특별자치도", "open_authority_code": "6530000"},
    "50110": {"name": "제주시", "province": "제주특별자치도", "open_authority_code": "6500000"},
    "52110": {"name": "전주시", "province": "전북특별자치도", "open_authority_code": "6540000"},
}


def mois_tourism_business_regions() -> list[dict[str, str]]:
    """Public safe choices for the UI; never expose provider-only code values."""
    return [{"id": region_id, "name": row["name"], "province": row["province"]}
            for region_id, row in MOIS_TOURISM_BUSINESS_REGIONS.items()]

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
        ProviderStatus("mois_tourism_business", bool(s.public_data_portal_api_key or s.kto_tourism_datalab_api_key), bool(s.mois_tourism_business_base_url)),
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

def _kto_error_message(response: httpx.Response) -> str | None:
    """Data.go.kr puts the real reason in cmmMsgHeader even on a 4xx/JSON body
    (e.g. LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR / 일일 서비스 요청제한
    횟수 초과). Surface that instead of a bare status code where possible."""
    try:
        body = response.json()
    except ValueError:
        return None
    header = body.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader") if isinstance(body, dict) else None
    if not isinstance(header, dict): return None
    return header.get("returnAuthMsg") or header.get("errMsg")

async def _get_with_retry(client: httpx.AsyncClient, url: str, params: dict[str, str],
                          headers: dict[str, str], retries: int = 3) -> httpx.Response:
    """Data.go.kr throttles bursts of concurrent requests with HTTP 429. Most
    of the time that's transient per-second throttling from fanning out
    several KTO calls per region at once, so retry with backoff. But a daily
    quota error (LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR with a
    '일일' reason) won't recover within this request — stop immediately
    instead of wasting the caller's time on retries that can't help."""
    delay = 0.4
    for attempt in range(retries + 1):
        response = await client.get(url, params=params, headers=headers)
        if response.status_code != 429: return response
        message = _kto_error_message(response)
        if attempt == retries or (message and "일일" in message): return response
        await asyncio.sleep(delay)
        delay *= 2
    return response

def _raise_for_status(response: httpx.Response, label: str) -> None:
    if not response.is_error: return
    message = _kto_error_message(response)
    detail = f"{label} returned HTTP {response.status_code}" + (f" ({message})" if message else "")
    raise RuntimeError(detail)

async def fetch_provider_json(provider: str, endpoint: str, params: dict[str, str]) -> object:
    """Fetch only paths relative to the approved provider base URL; keys stay server-side."""
    if endpoint.startswith(("http://", "https://")) or ".." in endpoint:
        raise ValueError("Endpoint must be a relative provider path")
    base_url, api_key, api_key_param = _provider_config(provider)
    # Public Data Portal shows the supplied key as "URL Encode". Decode once
    # before httpx creates the query string, preventing % from becoming %25.
    query = {**params, api_key_param: unquote(api_key)}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await _get_with_retry(client, urljoin(base_url, endpoint.lstrip("/")), query, {"Accept": "application/json"})
        _raise_for_status(response, provider)
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            return response.json()
        # The gateway supports JSON + XML. Preserve XML rather than treating a
        # valid XML response as a JSON decoding error; normalization follows in ETL.
        return {"format": "xml", "data": response.text}

def _configured_query(raw_query: str, key_name: str, api_key: str) -> dict[str, str]:
    """Parse a generated provider query safely and replace any copied secret."""
    parsed = dict(parse_qsl(raw_query.lstrip("?"), keep_blank_values=True))
    parsed.pop(key_name, None)
    parsed[key_name] = unquote(api_key)
    return parsed

async def fetch_kosis_statistics(query: str) -> object:
    """Call a user-configured KOSIS statistics query without exposing its key."""
    s = get_settings()
    if not s.kosis_api_key:
        raise ValueError("KOSIS API key is not configured")
    if not query:
        raise ValueError("KOSIS generated query is not configured")
    params = _configured_query(query, "apiKey", s.kosis_api_key)
    params.setdefault("method", "getList")
    params.setdefault("format", "json")
    endpoint = s.kosis_statistics_endpoint.strip("/")
    if endpoint.startswith(("http:", "https:", "..")):
        raise ValueError("KOSIS statistics endpoint must be a relative official path")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await _get_with_retry(client, f"{s.kosis_base_url.rstrip('/')}/{endpoint}", params,
                                         {"Accept": "application/json"})
        _raise_for_status(response, "KOSIS")
        return response.json()

def _mois_business_url(operation: str) -> str:
    """Build one of the two documented MOIS tourism-business operation URLs."""
    s = get_settings()
    if operation not in {"info", "history"}:
        raise ValueError("MOIS tourism-business operation must be info or history")
    if not s.mois_tourism_business_base_url:
        raise ValueError("MOIS tourism-business API base URL is not configured")
    parsed = urlsplit(s.mois_tourism_business_base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("MOIS tourism-business base URL must be an HTTPS URL")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/{operation}"


async def fetch_mois_tourism_business(operation: str, open_authority_code: str,
                                      base_date: str | None = None,
                                      page_no: int = 1, num_rows: int = 100) -> object:
    """Fetch approved 문화·관광사업자 records using its documented operations.

    ``/info`` returns the provider's current (two-days-lagged) status;
    ``/history`` requires an eight-digit BASE_DATE and returns a historic
    snapshot. ``OPN_ATMY_GRP_CD`` is a provider opening-authority code, not a
    KTO municipality code, so callers must pass the documented code explicitly.
    """
    s = get_settings()
    api_key = s.public_data_portal_api_key or s.kto_tourism_datalab_api_key
    if not api_key:
        raise ValueError("Data.go.kr API key is not configured")
    if not open_authority_code.strip():
        raise ValueError("MOIS open_authority_code (OPN_ATMY_GRP_CD) is required")
    if operation == "history" and (not base_date or not base_date.isdigit() or len(base_date) != 8):
        raise ValueError("MOIS history requires base_date in YYYYMMDD format")
    params = _configured_query(s.mois_tourism_business_query, "serviceKey", api_key)
    params.update({
        "serviceKey": unquote(api_key), "pageNo": str(page_no),
        "numOfRows": str(num_rows), "returnType": "JSON",
        "cond[OPN_ATMY_GRP_CD::EQ]": open_authority_code.strip(),
    })
    if operation == "history":
        params["cond[BASE_DATE::EQ]"] = base_date
    async with httpx.AsyncClient(timeout=30) as client:
        response = await _get_with_retry(client, _mois_business_url(operation), params, {"Accept": "application/json"})
        _raise_for_status(response, "MOIS tourism-business service")
        if "json" in response.headers.get("content-type", "").lower():
            return response.json()
        return {"format": "xml", "data": response.text}


async def fetch_mois_tourism_business_for_region(region_id: str, operation: str,
                                                 base_date: str | None = None,
                                                 page_no: int = 1,
                                                 num_rows: int = 100) -> object:
    """User-facing regional lookup: resolve a verified provider code server-side."""
    region = MOIS_TOURISM_BUSINESS_REGIONS.get(region_id)
    if not region:
        raise ValueError("This municipality is not available in the tourism-business list yet")
    return await fetch_mois_tourism_business(operation, region["open_authority_code"],
                                             base_date, page_no, num_rows)


async def fetch_mois_city_business_summary(region_id: str, operation: str,
                                           base_date: str | None = None) -> dict[str, object]:
    """City-specific 문화·관광사업자 summary.

    OPN_ATMY_GRP_CD only filters down to the *province* reporting group (see
    MOIS_TOURISM_BUSINESS_REGIONS's comment — verified empirically, not
    documented), so a single page for that code mixes in every other city
    in the province. This fetches every page for the group and keeps only
    rows whose address actually contains the target city's name, so a
    result labelled "경주시" is never really 경상북도-wide.
    """
    region = MOIS_TOURISM_BUSINESS_REGIONS.get(region_id)
    if not region:
        raise ValueError("This municipality is not available in the tourism-business list yet")
    city_name = str(region["name"])
    page_no, num_rows, all_items = 1, 100, []
    while True:
        raw = await fetch_mois_tourism_business(operation, str(region["open_authority_code"]), base_date, page_no, num_rows)
        page_items = _json_envelope_items(normalize_kto_xml(raw))
        all_items.extend(page_items)
        total = None
        if isinstance(raw, dict) and isinstance(raw.get("response"), dict):
            body = raw["response"].get("body")
            if isinstance(body, dict):
                try: total = int(body.get("totalCount"))
                except (TypeError, ValueError): total = None
        if not page_items or total is None or page_no * num_rows >= total or page_no >= 20:
            break
        page_no += 1
    province_total = len(all_items)
    city_items = [item for item in all_items
                 if city_name in str(item.get("ROAD_NM_ADDR") or item.get("LOTNO_ADDR") or "")]
    summary = summarize_mois_tourism_business({"items": city_items})
    summary["province_group_record_count"] = province_total
    summary["low_sample"] = province_total < 20
    return summary

def summarize_mois_tourism_business(payload: object) -> dict[str, object]:
    """Preserve raw MOIS records while making the lodging-supply proxy explicit.

    The service is a business-register source, not a room inventory. We count
    only rows explicitly labelled as operating and tourism-accommodation; no
    unknown status code is silently treated as open.
    """
    items = _json_envelope_items(normalize_kto_xml(payload))
    inactive_markers = ("폐업", "휴업", "정지", "취소")
    active_rows = [item for item in items if "영업" in str(item.get("SALS_STTS_NM", ""))
                   and not any(marker in str(item.get("SALS_STTS_NM", ""))
                               for marker in inactive_markers)]
    lodging_rows = [item for item in active_rows if "관광숙박" in str(item.get("CULTR_SPTS_TPBIZ_NM", ""))]
    return {
        "source": "행정안전부_문화_관광사업자 조회서비스",
        "raw_record_count": len(items),
        "operating_business_count": len(active_rows),
        "operating_tourism_accommodation_business_count": len(lodging_rows),
        "metric_type": "원자료 집계: 영업 중 관광숙박업소 수",
        "not_a_room_count": True,
        "items": items,
    }

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
    async with _kto_request_semaphore:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await _get_with_retry(client, url, query, {"Accept": "application/json"})
            _raise_for_status(response, f"KTO {service}/{endpoint}")
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

async def fetch_municipal_hub_attractions(area_cd: str, base_ym: str) -> object:
    """Fetch up to 100 navigation-network hub attractions for a municipality."""
    # KTO exposes several parent cities only through their current districts.
    signgu_codes = sorted(metric_source_codes(area_cd))
    responses = await asyncio.gather(*[
        fetch_kto_catalog_service_by_path("LocgoHubTarService1", "areaBasedList1", {
            "baseYm": base_ym, "areaCd": code[:2], "signguCd": code,
            "numOfRows": "100", "pageNo": "1", "_type": "json",
        }) for code in signgu_codes
    ])
    return {"items": [item for response in responses for item in _json_envelope_items(response)]}

def compute_hub_spatial_spread(data: object) -> dict[str, object] | None:
    """Calculate the geographic spread of navigation hub-attraction coordinates.

    This is a transparent spatial-coverage proxy, not visit-share concentration:
    the score is the root-mean-square haversine distance from the attractions'
    geographic centroid, reported in kilometres. Hub rank is deliberately not
    treated as a cardinal visit count.
    """
    points: list[tuple[float, float]] = []
    items = _json_envelope_items(data)
    for item in items:
        try:
            lon, lat = float(item.get("mapX")), float(item.get("mapY"))
        except (TypeError, ValueError):
            continue
        if 124 <= lon <= 132 and 33 <= lat <= 39:
            points.append((lon, lat))
    if len(points) < 2:
        return None
    center_lon = sum(point[0] for point in points) / len(points)
    center_lat = sum(point[1] for point in points) / len(points)

    def distance_km(lon: float, lat: float) -> float:
        d_lat, d_lon = radians(lat - center_lat), radians(lon - center_lon)
        a = sin(d_lat / 2) ** 2 + cos(radians(center_lat)) * cos(radians(lat)) * sin(d_lon / 2) ** 2
        return 6371.0088 * 2 * asin(sqrt(a))

    distances = [distance_km(lon, lat) for lon, lat in points]
    return {
        "hub_count": len(points),
        "spread_km": round(sqrt(sum(value ** 2 for value in distances) / len(distances)), 2),
        "method": "중심 관광지 좌표의 지리적 중심으로부터 RMS 거리",
        "is_visit_share_dispersion": False,
    }

def summarize_attraction_concentration(data: object) -> dict[str, object] | None:
    """Summarize KTO's per-attraction 30-day crowding forecasts.

    ``cnctrRate`` is normalized within each attraction (its busiest time=100),
    not a visit share across attractions. It therefore cannot support a spatial
    HHI or a regional dispersion score. We expose only the mean forecast level
    and coverage metadata, keeping the spatial-dispersion axis pending.
    """
    items = _json_envelope_items(data)
    if not items: return None
    rates: list[float] = []
    dates: set[str] = set()
    for item in items:
        ymd = item.get("baseYmd")
        try: rate = float(item.get("cnctrRate") or 0)
        except (TypeError, ValueError): continue
        rates.append(rate)
        if ymd: dates.add(ymd)
    if not rates: return None
    return {
        "attractions_tracked": len({item.get("tAtsNm") for item in items if item.get("tAtsNm")}),
        "forecast_days": len(dates),
        "mean_crowding_rate": round(sum(rates) / len(rates), 1),
        "interpretation": "관광지별 자체 최혼잡 시점=100인 향후 30일 상대 혼잡도 평균",
    }

async def fetch_visitor_window(start_ymd: str, end_ymd: str) -> list[dict]:
    """Fetch every daily local-visitor record in [start_ymd, end_ymd], paginating as needed.

    Cached by (start_ymd, end_ymd): the result is identical no matter which
    area_cds a caller later filters it down to, so repeat stability requests
    for the same window (e.g. across several diagnosis-target switches on the
    same day) reuse one paginated fetch instead of re-running it — this
    endpoint alone can cost 6+ KTO calls per request.
    """
    async def _fetch() -> list[dict]:
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
    return await _cached(f"window:{start_ymd}:{end_ymd}", _fetch)

def compute_visitor_stability(items: list[dict], area_cds: list[str]) -> dict[str, dict[str, object]]:
    """Day-to-day outside-visitor volatility within the fetched window, per area.

    This is a short-window demand-stability proxy, scoped to the fetched day
    window (not §4.1's full annual seasonal cycle — that needs a
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

def _aggregate_national_visitors(items: list[dict]) -> dict[str, dict[str, object]]:
    """Sum same-day visitor records into one entry per municipality.

    The KTO daily feed (locgoRegnVisitrDDList) already returns every
    municipality nationwide in one call — no per-region fan-out needed for
    a same-day national ranking. Shared by the single-region snapshot and
    the nationwide demand scan so both read the exact same aggregation.
    """
    by_area: dict[str, dict[str, object]] = {}
    for item in items:
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
    return by_area

def _percentile_rank(current: float, all_values: list[float]) -> float:
    return round(100 * sum(value <= current for value in all_values) / len(all_values), 1) if all_values else 0.0

async def fetch_national_visitor_ranking(base_ymd: str) -> list[dict[str, object]]:
    """Nationwide same-day outside-visitor ranking, sorted descending.

    This is Step① (전국 관광수요 스캔) of the diagnosis pipeline: a single KTO
    call already covers every municipality for the day, so peer selection
    can be based on real national demand instead of a fixed sample list.

    Cached by base_ymd. Both /national-ranking and /national-peers need this
    same scan on every page load and every diagnosis-target switch — without
    caching, that's two full KTO calls for identical same-day data every time.
    """
    async def _fetch() -> list[dict[str, object]]:
        raw = await fetch_kto_regional_visitors("local", base_ymd, base_ymd, 1, 1000)
        data = normalize_kto_xml(raw)
        if not isinstance(data, dict) or data.get("result_code") != "0000":
            raise ValueError("KTO visitor response could not be normalized")
        by_area = _aggregate_national_visitors(data.get("items", []))
        all_values = [float(entry["outside_visitors"]) for entry in by_area.values()]
        ranking = sorted(by_area.values(), key=lambda entry: -float(entry["outside_visitors"]))
        for index, entry in enumerate(ranking):
            entry["rank"] = index + 1
            entry["percentile"] = _percentile_rank(float(entry["outside_visitors"]), all_values)
        return ranking
    return await _cached(f"ranking:{base_ymd}", _fetch)

async def fetch_national_visitor_ranking_window(end_ymd: str, days: int = 7) -> list[dict[str, object]]:
    """Multi-day-average nationwide outside-visitor ranking, ending on end_ymd.

    A single day's demand can flip a region's national position or Peer
    Group membership on an ordinary weekday/weekend swing (a beach city
    scanned on a Saturday looks completely different from the same city on
    a Tuesday). This averages daily outside-visitors over `days` calendar
    days ending on end_ymd — one full weekly cycle by default — before
    ranking, so demand percentile and peer selection reflect a stable
    position rather than one day's noise.

    Reuses fetch_visitor_window, so whenever this window matches the one
    /analysis/visitor-stability requests for the same diagnosis (same
    end date, same day count), the two endpoints share one cached
    paginated KTO fetch instead of issuing it twice.
    """
    async def _fetch() -> list[dict[str, object]]:
        end = date(int(end_ymd[:4]), int(end_ymd[4:6]), int(end_ymd[6:8]))
        start = end - timedelta(days=days - 1)
        items = await fetch_visitor_window(start.strftime("%Y%m%d"), end_ymd)
        by_area = _aggregate_national_visitors(items)
        for entry in by_area.values():
            entry["outside_visitors"] = round(float(entry["outside_visitors"]) / days, 1)
            entry["resident_visitors"] = round(float(entry["resident_visitors"]) / days, 1)
            entry["foreign_visitors"] = round(float(entry["foreign_visitors"]) / days, 1)
        all_values = [float(entry["outside_visitors"]) for entry in by_area.values()]
        ranking = sorted(by_area.values(), key=lambda entry: -float(entry["outside_visitors"]))
        for index, entry in enumerate(ranking):
            entry["rank"] = index + 1
            entry["percentile"] = _percentile_rank(float(entry["outside_visitors"]), all_values)
        return ranking
    return await _cached(f"ranking_window:{end_ymd}:{days}", _fetch)

def classify_admin_type(area_name: str) -> str:
    """"시" / "군" / "자치구" / "일반구" / "기타".

    KTO's area_name encodes the distinction we need without any extra
    lookup: a sub-city 일반구 (수원시 팔달구, 청주시 상당구— not a 기초지자체
    in its own right, just an internal division of its parent 시) always
    carries a space, while a 자치구 (강남구, 해운대구 — itself a 기초지자체
    directly under a 특별시/광역시) never does. 시/군 need no such split.
    """
    if area_name.endswith("시"): return "시"
    if area_name.endswith("군"): return "군"
    if area_name.endswith("구"): return "일반구" if " " in area_name else "자치구"
    return "기타"

def is_independent_municipality(area_name: str) -> bool:
    """기초지자체 여부 — 일반구(및 그 밖의 비정형 코드)는 독립된 정책분석 단위가
    아니므로 진단 대상·전국 위치·Peer Group 어디에도 포함하지 않는다."""
    return classify_admin_type(area_name) not in ("일반구", "기타")

_CAPITAL_REGION_PROVINCES = {"서울특별시", "인천광역시", "경기도"}

def _load_region_centroids() -> dict[str, dict[str, object]]:
    path = Path(__file__).resolve().parent / "kr_sigungu_centroids.json"
    with path.open(encoding="utf-8") as file:
        return json.load(file)

def _load_region_areas() -> dict[str, float]:
    path = Path(__file__).resolve().parent / "kr_sigungu_area.json"
    with path.open(encoding="utf-8") as file:
        return json.load(file)

_REGION_CENTROIDS = _load_region_centroids()
_REGION_AREAS = _load_region_areas()

def resolve_region_province(area_cd: str, area_name: str) -> str | None:
    """지역의 소속 시·도를 확인한다. 직접 코드가 있으면 그대로, 없으면(예:
    구가 있는 시의 방문자 원천 집계 코드) 이름이 그 시로 시작하는 구들의
    소속 시·도를 사용한다 — 프론트엔드 resolveCentroid()와 동일한 규칙."""
    direct = _REGION_CENTROIDS.get(area_cd)
    if direct: return str(direct["province"])
    for entry in _REGION_CENTROIDS.values():
        if str(entry["name"]).startswith(area_name):
            return str(entry["province"])
    return None

def resolve_region_area(area_cd: str, area_name: str) -> float | None:
    """지역 면적(km²). 통계청 SGIS 행정동 경계를 시군구 단위로 합산해 계산했다
    (구면 좌표 등장방형 근사 — 경주시 1,323.5km² vs 공식 1,324.4km², 강남구
    38.3km² vs 공식 39.5km², 오차 3% 이내). 직접 코드가 없으면(구가 있는
    시의 방문자 원천 집계 코드) 그 시 이름으로 시작하는 구들의 면적을
    합산한다 — 중심좌표와 달리 면적은 평균이 아니라 합이어야 시 전체
    면적이 된다."""
    direct = _REGION_AREAS.get(area_cd)
    if direct is not None: return direct
    matching_codes = [code for code, entry in _REGION_CENTROIDS.items() if str(entry["name"]).startswith(area_name)]
    if not matching_codes: return None
    return sum(_REGION_AREAS.get(code, 0.0) for code in matching_codes) or None

def is_capital_region(province: str | None) -> bool:
    return province in _CAPITAL_REGION_PROVINCES

KOSIS_POPULATION_ORG_ID = "101"
KOSIS_POPULATION_TABLE_ID = "DT_1B04005N"  # 행정구역(읍면동)별/5세별 주민등록인구

async def fetch_population_by_codes(area_cds: list[str], base_ym: str) -> dict[str, float]:
    """주민등록인구(총인구수) per area_cd from KOSIS, in the exact same 5-digit
    code scheme KTO uses (verified directly: 47130/11680/41110 all resolve to
    the right 시군구/자치구 population without any remapping). One batched
    request covers the whole candidate pool. Returns {} (never raises) on any
    failure — population/density similarity is a refinement, not a hard
    dependency, so a KOSIS outage must degrade to demand-only peer selection,
    not break the whole Peer Group.
    """
    s = get_settings()
    if not s.kosis_api_key or not area_cds:
        return {}

    async def _fetch() -> dict[str, float]:
        params = {
            "method": "getList", "apiKey": s.kosis_api_key, "itmId": "T2",
            "objL1": "+".join(area_cds), "objL2": "0", "format": "json", "jsonVD": "Y",
            "prdSe": "M", "startPrdDe": base_ym, "endPrdDe": base_ym,
            "orgId": KOSIS_POPULATION_ORG_ID, "tblId": KOSIS_POPULATION_TABLE_ID,
        }
        endpoint = s.kosis_statistics_endpoint.strip("/")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{s.kosis_base_url.rstrip('/')}/{endpoint}", params=params,
                                        headers={"Accept": "application/json"})
            if response.is_error: return {}
            try:
                rows = response.json()
            except ValueError:
                return {}
            if not isinstance(rows, list): return {}
            result: dict[str, float] = {}
            for row in rows:
                code, value = row.get("C1"), row.get("DT")
                if not code or value is None: continue
                try: result[str(code)] = float(value)
                except (TypeError, ValueError): continue
            return result

    try:
        key = f"population:{base_ym}:{','.join(sorted(area_cds))}"
        return await _cached(key, _fetch, ttl=6 * 3600.0)
    except Exception:
        return {}

def _quantile(values: list[float], q: float) -> float | None:
    """Linear-interpolation quantile (q in [0,1]); None for an empty sample."""
    if not values: return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

def _percentile_within(value: float, pool_values: list[float]) -> float:
    return _percentile_rank(value, pool_values) if pool_values else 50.0

def classify_pg_category(admin_type: str, density_percentile_within_type: float | None) -> str | None:
    """PG-1~4: a density-based city-character label layered ON TOP OF the
    행정유형(시/군/자치구) split — never a replacement for it, and deliberately
    built from population density alone, not from tourism demand or lodging
    supply (관광수요·숙박공급은 이 진단이 "취약한가"를 판정하는 그 지표들 자체다.
    이걸로 Peer Group을 나누면 같은 약점끼리 묶여 약점이 구조적으로 안 보이는
    자기참조 순환이 생긴다 — 이 프로젝트의 모든 Peer Group 로직이 지키는 원칙).

    density_percentile_within_type must be computed within the region's OWN
    admin-type pool (자치구끼리, 시/군끼리 따로) — comparing a 자치구's density
    against a 군's would always rank the 자치구 "denser" and say nothing about
    which 자치구 specifically stands out among other 자치구.

    - 자치구, 밀도 상위 50%ile 초과 → PG-1 (도심/상업 집중형 — 예: 종로구·중구)
    - 자치구, 밀도 50%ile 이하     → PG-3 (대도시 주거/위성형 — 예: 일반 주거 자치구)
    - 시/군, 밀도 상위 50%ile 초과   → PG-2 (도농복합 관광거점형 — 예: 경주시·강릉시)
    - 시/군, 밀도 50%ile 이하       → PG-4 (일반 지방/농어촌형 — 예: 대다수 군)

    Returns None when admin_type is unrecognized or density data is missing —
    never guesses a category without density data behind it.
    """
    if admin_type not in ("시", "군", "자치구") or density_percentile_within_type is None:
        return None
    # _percentile_rank는 "이 값 이하인 후보 수 / 전체" 방식이라, 표본이 2곳뿐이면
    # 더 작은 값도 정확히 50이 나온다(자기 자신 포함 1/2). >= 50으로 자르면 그
    # 경계값이 "상위" 쪽으로 잘못 들어간다 — 엄격 부등호로 그 경계 사례를 하위에 둔다.
    dense_half = density_percentile_within_type > 50
    if admin_type == "자치구":
        return "PG-1" if dense_half else "PG-3"
    return "PG-2" if dense_half else "PG-4"

PG_CATEGORY_LABELS = {
    "PG-1": "도심/상업 집중형", "PG-2": "도농복합 관광거점형",
    "PG-3": "대도시 주거/위성형", "PG-4": "일반 지방/농어촌형",
}

async def build_pg_categories(independent_ranking: list[dict[str, object]], base_ym: str) -> dict[str, str]:
    """National, diagnosis-independent PG-1~4 assignment for every 기초지자체.

    Computed once over the *whole* national pool (not the smaller, target-
    specific candidate pool build_peer_group assembles) so a region's PG
    category is a stable property of that region — "경주시 is PG-2" — not
    something that shifts depending on which other region's diagnosis screen
    happens to be open. One batched KOSIS population call regardless of how
    many municipalities are being classified.
    """
    codes = [str(entry["area_cd"]) for entry in independent_ranking]
    population_by_code = await fetch_population_by_codes(codes, base_ym)
    by_admin_type: dict[str, list[dict[str, object]]] = {}
    for entry in independent_ranking:
        by_admin_type.setdefault(classify_admin_type(str(entry["area_name"])), []).append(entry)

    result: dict[str, str] = {}
    for admin_type, entries in by_admin_type.items():
        densities: dict[str, float] = {}
        for entry in entries:
            area_cd, area_name = str(entry["area_cd"]), str(entry["area_name"])
            population = population_by_code.get(area_cd)
            area_km2 = resolve_region_area(area_cd, area_name)
            if population is not None and area_km2:
                densities[area_cd] = population / area_km2
        pool_values = list(densities.values())
        for entry in entries:
            area_cd = str(entry["area_cd"])
            density = densities.get(area_cd)
            percentile = _percentile_rank(density, pool_values) if density is not None else None
            category = classify_pg_category(admin_type, percentile)
            if category is not None:
                result[area_cd] = category
    return result

async def build_peer_group(ranking: list[dict[str, object]], target_area_cd: str, target_area_name: str,
                           count: int, base_ym: str, pg_categories: dict[str, str] | None = None) -> dict[str, object]:
    """Structural-condition peer group, NOT a "top-N by national demand" list.

    Peers must share the target's 행정유형(시/군/자치구) — a 시 is never
    benchmarked against a 자치구, since city-center districts (dense
    transit/lodging/business-tourism demand) and provincial cities aren't
    the same kind of place. For 시/군, peers are additionally restricted to
    the same 수도권 여부, since a capital-region 시 and a non-capital
    provincial 시 have structurally different demand bases.

    Within that pool, peers are ranked by a combined structural-similarity
    distance across national demand scale, population, and population
    density — each converted to a percentile *within this same pool* first,
    so the three end up on a comparable 0–100 scale despite very different
    raw units (visitor counts vs. person counts vs. person/km²). A candidate
    missing population/area data still ranks — its distance just falls back
    to whichever dimensions are actually available for it, rather than being
    dropped or crashing the whole comparison.

    Deliberately does not use any performance/outcome variable (stay, spend,
    lodging, ...) to form the group — only administrative type, capital-
    region status, demand scale, population, and density, so the peer group
    is the target's *condition*, not a self-referential slice of the very
    outcomes being diagnosed.

    pg_categories (optional): a density-based PG-1~4 label per area_cd from
    build_pg_categories(). When supplied, same-category candidates are
    preferred within the ranking — not filtered — so a thin admin-type pool
    doesn't get thinner still.
    """
    admin_type = classify_admin_type(target_area_name)
    target_province = resolve_region_province(target_area_cd, target_area_name)
    target_capital = is_capital_region(target_province)
    target_entry = next((entry for entry in ranking if entry["area_cd"] == target_area_cd), None)
    target_percentile = float(target_entry["percentile"]) if target_entry else None

    pool = [entry for entry in ranking
            if entry["area_cd"] != target_area_cd and classify_admin_type(str(entry["area_name"])) == admin_type]

    relaxed = False
    if admin_type in ("시", "군"):
        capital_matched = [entry for entry in pool
                           if is_capital_region(resolve_region_province(str(entry["area_cd"]), str(entry["area_name"]))) == target_capital]
        if len(capital_matched) >= min(count, 3):
            pool = capital_matched
        else:
            relaxed = True  # too few same-수도권-status candidates; fall back to admin-type only

    # 인구·인구밀도: 대상 + 후보 풀 전체를 한 번에 조회한 뒤, 이 풀 안에서의
    # 백분위로 변환한다(단위가 다른 세 지표를 같은 0~100 척도로 비교하기 위함).
    all_codes = [target_area_cd] + [str(entry["area_cd"]) for entry in pool]
    population_by_code = await fetch_population_by_codes(all_codes, base_ym)

    def _density(area_cd: str, area_name: str) -> float | None:
        population = population_by_code.get(area_cd)
        area_km2 = resolve_region_area(area_cd, area_name)
        return population / area_km2 if population is not None and area_km2 else None

    pool_populations = [population_by_code[str(e["area_cd"])] for e in pool if str(e["area_cd"]) in population_by_code]
    pool_densities = [d for e in pool if (d := _density(str(e["area_cd"]), str(e["area_name"]))) is not None]
    target_population = population_by_code.get(target_area_cd)
    target_density = _density(target_area_cd, target_area_name)
    target_population_pct = _percentile_within(target_population, pool_populations) if target_population is not None else None
    target_density_pct = _percentile_within(target_density, pool_densities) if target_density is not None else None
    has_structure_data = bool(pool_populations) and target_population_pct is not None

    target_pg_category = pg_categories.get(target_area_cd) if pg_categories else None

    def distance(entry: dict[str, object]) -> float:
        area_cd, area_name = str(entry["area_cd"]), str(entry["area_name"])
        diffs: list[float] = []
        if target_percentile is not None:
            diffs.append(abs(float(entry["percentile"]) - target_percentile))
        population = population_by_code.get(area_cd)
        if population is not None and target_population_pct is not None:
            diffs.append(abs(_percentile_within(population, pool_populations) - target_population_pct))
        density = _density(area_cd, area_name)
        if density is not None and target_density_pct is not None:
            diffs.append(abs(_percentile_within(density, pool_densities) - target_density_pct))
        base = sum(diffs) / len(diffs) if diffs else 100.0  # no comparable data at all: sort last
        # PG-1~4는 필터가 아니라 "얹는" 정렬 기준이다 — 같은 행정유형·수도권
        # 안에서도 도시 성격(밀도대)이 같은 후보를 앞세우되, PG가 다르다는
        # 이유만으로 후보를 완전히 배제하지는 않는다(admin_type 분리를 한 번
        # 더 쪼개면 이미 얇은 Peer 표본이 더 얇아진다).
        if pg_categories and target_pg_category is not None:
            entry_pg = pg_categories.get(area_cd)
            if entry_pg is not None and entry_pg != target_pg_category:
                base += 15.0
        return base

    pool = sorted(pool, key=distance)
    peers = []
    for entry in pool[:count]:
        area_cd, area_name = str(entry["area_cd"]), str(entry["area_name"])
        population = population_by_code.get(area_cd)
        density = _density(area_cd, area_name)
        peers.append({**entry, "population": population,
                      "population_density": round(density, 1) if density is not None else None,
                      "pg_category": pg_categories.get(area_cd) if pg_categories else None})

    base_note = f"{'수도권' if target_capital else '비수도권'} {admin_type}" if admin_type in ("시", "군") else admin_type
    region_note = f"{base_note} · 관광수요·인구·인구밀도 규모가 유사한 지역" if has_structure_data \
        else f"{base_note} · 관광수요 규모가 유사한 지역 (인구 데이터 미확보로 인구·밀도는 이번 비교에서 제외)"
    if target_pg_category is not None:
        region_note += f" · {PG_CATEGORY_LABELS[target_pg_category]}({target_pg_category}) 우선"
    return {
        "admin_type": admin_type, "capital_region": target_capital, "relaxed": relaxed,
        "criteria_note": region_note, "peers": peers, "pg_category": target_pg_category,
        "target_population": target_population, "target_population_density": round(target_density, 1) if target_density is not None else None,
    }

def build_live_visitor_snapshot(payload: object, area_cd: str, base_ymd: str) -> dict[str, object]:
    """Turn the KTO GW response into a traceable, non-modelled live snapshot.

    TCEI and R-GAP are intentionally not inferred here: those measures need
    stay, spend, dispersion and seasonal inputs. Returning null prevents a
    visitor-only feed from being presented as a completed R-GAP diagnosis.
    """
    data = normalize_kto_xml(payload)
    if not isinstance(data, dict) or data.get("result_code") != "0000":
        raise ValueError("KTO visitor response could not be normalized")
    by_area = _aggregate_national_visitors(data.get("items", []))
    target = by_area.get(area_cd)
    if not target:
        raise ValueError("Selected municipality is not present in this KTO daily response")
    all_values = [float(entry["outside_visitors"]) for entry in by_area.values()]
    current = float(target["outside_visitors"])
    percentile = _percentile_rank(current, all_values)
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
            "message": "선택일 방문자 추정치는 반영됐습니다. 체류·소비·공간분산·연간 계절성 지표가 적재되면 TCEI와 R-GAP을 산출합니다.",
            "missing_inputs": ["체류·숙박", "관광소비", "공간분산", "월별 계절성"],
        },
    }
