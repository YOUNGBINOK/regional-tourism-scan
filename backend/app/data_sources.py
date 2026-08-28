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
        if category == "외지인":
            entry["outside_visitors"] = float(entry["outside_visitors"]) + value
        elif category == "외국인":
            entry["foreign_visitors"] = float(entry["foreign_visitors"]) + value
        elif category == "현지인":
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
