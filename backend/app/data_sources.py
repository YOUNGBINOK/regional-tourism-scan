"""Provider boundary for KTO Data Lab and public-data ingestion.

Endpoint paths stay configurable because the approved service/API product can
differ by Data Lab account and competition scope.
"""
from dataclasses import dataclass
from copy import deepcopy
import asyncio
import time
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

async def _cached(key: str, factory, ttl: float = _CACHE_TTL_SECONDS) -> object:
    entry = _response_cache.get(key)
    if entry is not None and time.monotonic() - entry[0] <= ttl:
        return entry[1]
    value = await factory()
    _response_cache[key] = (time.monotonic(), value)
    return value

KTO_SERVICE_CATALOG = {
    "regional_visitors": {"service": "DataLabService", "endpoints": {"metro": "metcoRegnVisitrDDList", "local": "locgoRegnVisitrDDList"}, "coverage": ["외지인·현지인·외국인 일별 방문자"], "integration_status": "verified"},
    "demand_intensity": {"service": "AreaTarDemDsService", "endpoints": {"stay": "areaTarSjrnDsList", "spend": "areaTarExpDsList"}, "coverage": ["숙박 비중·숙박일수별 방문자", "외지인 소비액·소비 비중·방문량 대비 소비"], "integration_status": "verified"},
    "tourism_diversity": {"service": "AreaTarDivService", "endpoints": {"visitor": "areaTouDivList", "spend": "areaExpDivList", "international": "areaIntlDivList"}, "coverage": ["연령별 방문객", "연령별 소비", "외국인 소비·국적 다양성"], "integration_status": "verified"},
    "attraction_concentration": {"service": "TatsCnctrRateService", "endpoints": {}, "coverage": ["관광지 30일 집중률 예측"], "integration_status": "awaiting_operation_name", "note": "승인된 동일 키를 사용합니다. 활용신청 상세의 오퍼레이션명만 환경변수에 입력하면 호출됩니다."},
    "municipal_hub_attractions": {"service": "LocgoHubTarService1", "endpoints": {"default": "areaBasedList1"}, "coverage": ["내비게이션 연계 중심 관광지 순위·좌표"], "integration_status": "verified"},
    "tourism_resource_demand": {"service": "AreaTarResDemService", "endpoints": {}, "coverage": ["SNS·카드·내비 기반 관광 자원 수요"], "integration_status": "awaiting_operation_name", "note": "승인된 동일 키를 사용합니다. 활용신청 상세의 오퍼레이션명만 환경변수에 입력하면 호출됩니다."},
}

# 행정안전부 지방행정 인허가 데이터의 개방자치단체코드. 화면에는 지역명만
# 노출하고 서버에서 코드로 변환한다. 현재 KTO 실시간 진단에 제공 중인 4개
# 지역부터 시작하며, 전국 적재 단계에서 공식 코드표 전체로 확장한다.
MOIS_TOURISM_BUSINESS_REGIONS = {
    "47130": {"name": "경주시", "province": "경상북도", "open_authority_code": "5050000"},
    "51150": {"name": "강릉시", "province": "강원특별자치도", "open_authority_code": "4201000"},
    "50110": {"name": "제주시", "province": "제주특별자치도", "open_authority_code": "6510000"},
    "52110": {"name": "전주시", "province": "전북특별자치도", "open_authority_code": "4641000"},
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
    # KTO exposes Jeonju through its two current district codes rather than the
    # legacy city aggregate used by the daily visitor feed.
    signgu_codes = ["52111", "52113"] if area_cd == "52110" else [area_cd]
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

def is_city_level(area_name: str) -> bool:
    """True for a 시/군 aggregate (경주시, 완주군, 세종특별자치시, ...), false for
    any 구 — both a sub-city district ("수원시 팔달구", which KTO's area_name
    always carries as "OO시 XX구" with a space) and a standalone metro-city
    district (강남구, 해운대구). Diagnosis targets and their peer comparison
    group are both restricted to this granularity so a 시 is never benchmarked
    against a 구, which isn't the same kind of administrative unit."""
    return area_name.endswith("시") or area_name.endswith("군")

def select_national_peers(ranking: list[dict[str, object]], exclude_area_cd: str, count: int) -> list[dict[str, object]]:
    """Top-N other city/county-level municipalities by national demand —
    "이미 잘 오는 지역들". ``ranking`` is already sorted descending by demand,
    so this is simply the highest-demand 시/군 other than the one being
    diagnosed: the peer group used to reveal a hidden weakness within an
    already-successful cohort, not an arbitrary or hardcoded sample. 구-level
    entries are excluded so a 시 is only ever compared against other 시/군.
    """
    return [entry for entry in ranking
            if entry["area_cd"] != exclude_area_cd and is_city_level(str(entry["area_name"]))][:count]

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
