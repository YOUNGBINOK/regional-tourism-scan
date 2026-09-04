import asyncio

import pytest

from app.data_sources import (compute_hub_spatial_spread, compute_visitor_stability,
                              summarize_attraction_concentration,
                              summarize_mois_tourism_business, fetch_mois_city_business_summary,
                              mois_tourism_business_regions,
                              _aggregate_national_visitors, _percentile_rank, _quantile,
                              classify_admin_type, is_independent_municipality,
                              is_capital_region, resolve_region_province, resolve_region_area,
                              build_peer_group, fetch_national_visitor_ranking_window,
                              classify_pg_category, build_pg_categories,
                              build_live_visitor_snapshot, _cached,
                              _response_cache, _inflight_cache,
                              metric_source_codes)


async def _no_population(area_cds: list, base_ym: str) -> dict:
    return {}


def test_cached_coalesces_simultaneous_provider_requests():
    """A cold start must not spend one KTO call for every simultaneous UI fetch."""
    async def run():
        _response_cache.clear()
        _inflight_cache.clear()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {"ok": True}

        values = await asyncio.gather(*[_cached("coalesce-test", factory) for _ in range(6)])
        assert calls == 1
        assert values == [{"ok": True}] * 6

    asyncio.run(run())


def test_kto_analytics_expands_parent_cities_to_their_district_codes():
    # The screenshot regression: the daily visitor ranking calls these
    # municipalities "수원시/천안시/용인시", but the KTO index endpoints return
    # only the lower-level 구 rows. Their aggregate must not become "--".
    assert metric_source_codes("41110") == {"41111", "41113", "41115", "41117"}
    assert metric_source_codes("44130") == {"44131", "44133"}
    assert metric_source_codes("41460") == {"41461", "41463", "41465"}
    assert metric_source_codes("41290") == {"41290"}


def _build_peer_group_without_population(monkeypatch, ranking, target_cd, target_name, count):
    """Most peer-group tests care about admin-type/demand-scale behavior, not
    population — stub the KOSIS call so they stay fast, offline, and
    deterministic, and incidentally cover the "no population data" fallback
    path every real request can also hit (missing key, KOSIS outage, ...)."""
    monkeypatch.setattr("app.data_sources.fetch_population_by_codes", _no_population)
    return asyncio.run(build_peer_group(ranking, target_cd, target_name, count, base_ym="202607"))


def _visitor_item(code: str, name: str, category: str, count: str) -> dict:
    return {"signguCode": code, "signguNm": name, "touDivNm": category, "touNum": count,
            "baseYmd": "20260701", "daywkDivCd": "4", "daywkDivNm": "목요일"}


def _ranking(items: list[dict]) -> list[dict]:
    by_area = _aggregate_national_visitors(items)
    values = [float(entry["outside_visitors"]) for entry in by_area.values()]
    ranking = sorted(by_area.values(), key=lambda entry: -float(entry["outside_visitors"]))
    for entry in ranking:
        entry["percentile"] = _percentile_rank(float(entry["outside_visitors"]), values)
    return ranking


def test_classify_admin_type_distinguishes_gu_from_si_gun():
    assert classify_admin_type("경주시") == "시"
    assert classify_admin_type("완주군") == "군"
    assert classify_admin_type("세종특별자치시") == "시"
    assert classify_admin_type("강남구") == "자치구"  # 기초지자체
    assert classify_admin_type("수원시 팔달구") == "일반구"  # 기초지자체 아님


def test_is_independent_municipality_excludes_only_ilban_gu():
    assert is_independent_municipality("경주시") is True
    assert is_independent_municipality("강남구") is True
    assert is_independent_municipality("수원시 팔달구") is False


def test_build_peer_group_never_mixes_admin_types(monkeypatch):
    # 강남구(자치구) has far higher demand than every 시/군 here, but a 시
    # target must only ever be benchmarked against other 시/군 — 구 is a
    # different kind of administrative unit, not simply a smaller peer.
    items = [
        _visitor_item("11680", "강남구", "외지인(b)", "900"),
        _visitor_item("47130", "경주시", "외지인(b)", "300"),
        _visitor_item("51150", "강릉시", "외지인(b)", "200"),
        _visitor_item("52130", "군산시", "외지인(b)", "150"),
    ]
    ranking = _ranking(items)
    group = _build_peer_group_without_population(monkeypatch, ranking, "47130", "경주시", count=3)
    assert group["admin_type"] == "시"
    peer_cds = [peer["area_cd"] for peer in group["peers"]]
    assert "11680" not in peer_cds  # the 자치구 must never appear as a 시's peer
    assert set(peer_cds) == {"51150", "52130"}


def test_build_peer_group_picks_closest_demand_scale_without_population_data(monkeypatch):
    # Five 시 with descending demand; with no population data available (the
    # fallback every real request can also hit), similarity falls back to
    # demand scale alone: the target (C, rank 3) should be benchmarked
    # against the two *closest* in demand (B and D), not the top performers.
    items = [
        _visitor_item("10", "가시", "외지인(b)", "500"),
        _visitor_item("20", "나시", "외지인(b)", "400"),
        _visitor_item("30", "다시", "외지인(b)", "300"),
        _visitor_item("40", "라시", "외지인(b)", "200"),
        _visitor_item("50", "마시", "외지인(b)", "100"),
    ]
    ranking = _ranking(items)
    group = _build_peer_group_without_population(monkeypatch, ranking, "30", "다시", count=2)
    assert [peer["area_cd"] for peer in group["peers"]] == ["20", "40"]
    assert "인구 데이터 미확보" in group["criteria_note"]


def test_build_peer_group_uses_population_and_density_when_available(monkeypatch):
    # Same demand for every candidate (so demand alone can't distinguish
    # them), but the target's population/density profile clearly matches C
    # over B or D — the peer group must pick up on that structural signal.
    items = [
        _visitor_item("10", "가시", "외지인(b)", "100"),
        _visitor_item("20", "나시", "외지인(b)", "100"),
        _visitor_item("30", "다시", "외지인(b)", "100"),
        _visitor_item("40", "라시", "외지인(b)", "100"),
    ]
    ranking = _ranking(items)

    async def fake_population(area_cds, base_ym):
        return {"10": 500_000.0, "20": 50_000.0, "30": 45_000.0, "40": 40_000.0}

    monkeypatch.setattr("app.data_sources.fetch_population_by_codes", fake_population)
    monkeypatch.setattr("app.data_sources.resolve_region_area", lambda area_cd, area_name: 100.0)
    group = asyncio.run(build_peer_group(ranking, "30", "다시", count=1, base_ym="202607"))
    assert [peer["area_cd"] for peer in group["peers"]] == ["40"]  # 45k is closer to 40k than to 500k/50k
    assert group["peers"][0]["population"] == 40_000.0
    assert "인구·인구밀도" in group["criteria_note"]


def test_capital_region_lookup_for_a_known_gyeonggi_and_non_capital_city():
    assert is_capital_region(resolve_region_province("41110", "수원시")) is True
    assert is_capital_region(resolve_region_province("47130", "경주시")) is False


def test_resolve_region_area_sums_districts_for_a_split_city():
    # 수원시's aggregate code isn't in the boundary file (only its 4 구 are),
    # so its area must be the *sum* of those districts, not an average like
    # the centroid lookup uses for coordinates.
    suwon_area = resolve_region_area("41110", "수원시")
    gyeongju_area = resolve_region_area("47130", "경주시")
    assert suwon_area is not None and gyeongju_area is not None
    assert 100 < suwon_area < 200  # Suwon is ~121 km²
    assert 1200 < gyeongju_area < 1400  # Gyeongju is ~1,324 km², a direct match


def test_national_ranking_window_averages_across_days_not_a_single_spike(monkeypatch):
    # A one-off spike day (a festival, a local holiday) must not carry the
    # same weight as a full window of steady demand — the windowed ranking
    # exists specifically so a single day can't dictate a region's national
    # position (fixes the "단일 일자 의존" weakness). 가시 gets one huge day
    # and two zero days (avg 333); 나시 gets 300 every day (avg 300) — 가시
    # still edges ahead here, but only because its *3-day average* does, not
    # because one spike day alone put it there.
    window_items = [
        _visitor_item("10", "가시", "외지인(b)", "1000"),
        {**_visitor_item("10", "가시", "외지인(b)", "0"), "baseYmd": "20260702"},
        {**_visitor_item("10", "가시", "외지인(b)", "0"), "baseYmd": "20260703"},
        _visitor_item("20", "나시", "외지인(b)", "300"),
        {**_visitor_item("20", "나시", "외지인(b)", "300"), "baseYmd": "20260702"},
        {**_visitor_item("20", "나시", "외지인(b)", "300"), "baseYmd": "20260703"},
    ]

    async def fake_window(start_ymd, end_ymd):
        return window_items

    monkeypatch.setattr("app.data_sources.fetch_visitor_window", fake_window)
    ranking = asyncio.run(fetch_national_visitor_ranking_window("20260703", days=3))
    by_code = {entry["area_cd"]: entry for entry in ranking}
    assert by_code["10"]["outside_visitors"] == pytest.approx(1000 / 3, rel=1e-3)
    assert by_code["20"]["outside_visitors"] == pytest.approx(300.0, rel=1e-3)


def test_classify_pg_category_splits_by_admin_type_then_density():
    assert classify_pg_category("자치구", 80) == "PG-1"   # 도심/상업 집중형
    assert classify_pg_category("자치구", 20) == "PG-3"   # 대도시 주거/위성형
    assert classify_pg_category("시", 80) == "PG-2"        # 도농복합 관광거점형
    assert classify_pg_category("군", 20) == "PG-4"        # 일반 지방/농어촌형
    assert classify_pg_category("일반구", 80) is None      # 기초지자체가 아닌 유형은 분류하지 않음
    assert classify_pg_category("시", None) is None        # 밀도 데이터 없이는 추측하지 않음


def test_pg_categories_computed_within_own_admin_type_pool_not_across_types(monkeypatch):
    # 자치구 두 곳(밀도 100/10)과 시 두 곳(밀도 100/10)을 섞어 넣는다. 자치구끼리,
    # 시끼리 각각 백분위를 매겨야 한다 — 전체를 한 풀로 섞으면 자치구 쪽 밀도가
    # 시 쪽보다 항상 높아 보여, 그 안에서 누가 상대적으로 밀집됐는지가 사라진다.
    ranking = [
        {"area_cd": "10", "area_name": "가구"},   # 자치구, 밀도 100 -> PG-1
        {"area_cd": "20", "area_name": "나구"},   # 자치구, 밀도 10  -> PG-3
        {"area_cd": "30", "area_name": "다시"},   # 시, 밀도 100 -> PG-2 (같은 시 풀 안에서는 최고밀도)
        {"area_cd": "40", "area_name": "라시"},   # 시, 밀도 10  -> PG-4
    ]

    async def fake_population(area_cds, base_ym):
        return {"10": 1000.0, "20": 100.0, "30": 1000.0, "40": 100.0}

    monkeypatch.setattr("app.data_sources.fetch_population_by_codes", fake_population)
    monkeypatch.setattr("app.data_sources.resolve_region_area",
                        lambda area_cd, area_name: 10.0)  # 밀도 = 인구/10, 모두 동일 면적
    categories = asyncio.run(build_pg_categories(ranking, base_ym="202607"))
    assert categories == {"10": "PG-1", "20": "PG-3", "30": "PG-2", "40": "PG-4"}


def test_build_peer_group_prefers_same_pg_category_without_excluding_others(monkeypatch):
    # 다섯 개 시가 관광수요 백분위상 대상(50)에 정확히 같은 거리(±10)로 두 곳씩
    # 있다. PG 카테고리가 같은 쪽(遠 PG-2)을 앞세워야 한다 — 다른 쪽을 아예
    # 제외하지는 않으므로 표본이 줄어들지 않는다는 것도 함께 확인한다.
    items = [
        _visitor_item("10", "가시", "외지인(b)", "600"),   # target, pct ~83
        _visitor_item("20", "나시", "외지인(b)", "500"),   # 근접, PG-2 (같음)
        _visitor_item("30", "다시", "외지인(b)", "300"),   # 근접, PG-4 (다름)
        _visitor_item("40", "라시", "외지인(b)", "100"),   # 더 멂
    ]
    ranking = _ranking(items)
    pg_categories = {"10": "PG-2", "20": "PG-2", "30": "PG-4", "40": "PG-4"}
    monkeypatch.setattr("app.data_sources.fetch_population_by_codes", _no_population)
    group_with_pg = asyncio.run(build_peer_group(ranking, "10", "가시", count=3, base_ym="202607",
                                                  pg_categories=pg_categories))
    peer_cds_with_pg = [peer["area_cd"] for peer in group_with_pg["peers"]]
    assert peer_cds_with_pg[0] == "20"  # 같은 PG-2가 최우선으로 온다
    assert set(peer_cds_with_pg) == {"20", "30", "40"}  # 그래도 후보가 빠지지는 않는다
    assert group_with_pg["pg_category"] == "PG-2"


def test_quantile_top_quarter_is_at_least_the_median():
    values = [10.0, 20.0, 30.0, 40.0]
    assert _quantile(values, 0.75) >= _quantile(values, 0.5)
    assert _quantile([], 0.75) is None


def test_national_visitor_percentile_covers_every_municipality_in_the_feed():
    # build_live_visitor_snapshot's percentile must be computed across every
    # municipality present in the daily feed, not a fixed sample — this is
    # what makes "전국 관광수요 스캔" real rather than a 3-4 city comparison.
    items = [_visitor_item(str(code), f"지역{code}", "외지인(b)", str(count))
             for code, count in [(1, 10), (2, 20), (3, 30), (4, 40), (5, 50)]]
    payload = {"format": "xml", "data": (
        "<response><header><resultCode>0000</resultCode></header><body>"
        "<items>" + "".join(
            f"<item><signguCode>{c}</signguCode><signguNm>지역{c}</signguNm>"
            f"<touDivNm>외지인(b)</touDivNm><touNum>{n}</touNum><baseYmd>20260701</baseYmd></item>"
            for c, n in [(1, 10), (2, 20), (3, 30), (4, 40), (5, 50)]
        ) + "</items><pageNo>1</pageNo><numOfRows>5</numOfRows><totalCount>5</totalCount></body></response>"
    )}
    snapshot = build_live_visitor_snapshot(payload, "3", "20260701")
    assert snapshot["national_comparison"]["municipality_count"] == 5
    # Region "3" (30) sits exactly at the middle: 3 of 5 values are <= it.
    assert snapshot["national_comparison"]["outside_visitor_percentile"] == 60.0


def test_concentration_forecast_is_not_spatial_dispersion():
    payload = {"response": {"body": {"items": {"item": [
        {"tAtsNm": "A", "baseYmd": "20260701", "cnctrRate": "100"},
        {"tAtsNm": "B", "baseYmd": "20260701", "cnctrRate": "50"},
    ]}}}}
    result = summarize_attraction_concentration(payload)
    assert result is not None
    assert result["mean_crowding_rate"] == 75.0
    assert "dispersion_index" not in result


def test_constant_daily_visitors_are_fully_stable():
    items = [
        {"signguCode": "47130", "touDivNm": "외지인(b)", "baseYmd": f"2026070{day}", "touNum": "100"}
        for day in range(1, 4)
    ]
    result = compute_visitor_stability(items, ["47130"])
    assert result["47130"]["stability_index"] == 100.0


def test_hub_spread_uses_coordinates_not_rank_as_visit_count():
    payload = {"response": {"body": {"items": {"item": [
        {"mapX": "129.20", "mapY": "35.80", "hubRank": "1"},
        {"mapX": "129.30", "mapY": "35.80", "hubRank": "99"},
    ]}}}}
    result = compute_hub_spatial_spread(payload)
    assert result is not None
    assert result["hub_count"] == 2
    assert result["spread_km"] > 0
    assert result["is_visit_share_dispersion"] is False


def test_mois_lodging_supply_is_business_count_not_room_count():
    payload = {"response": {"body": {"items": {"item": [
        {"SALS_STTS_NM": "영업", "CULTR_SPTS_TPBIZ_NM": "관광숙박업"},
        {"SALS_STTS_NM": "폐업", "CULTR_SPTS_TPBIZ_NM": "관광숙박업"},
        {"SALS_STTS_NM": "영업", "CULTR_SPTS_TPBIZ_NM": "여행업"},
    ]}}}}
    result = summarize_mois_tourism_business(payload)
    assert result["operating_tourism_accommodation_business_count"] == 1
    assert result["not_a_room_count"] is True


def test_mois_region_choices_do_not_expose_provider_authority_code():
    options = mois_tourism_business_regions()
    assert {option["name"] for option in options} >= {"경주시", "강릉시", "제주시", "전주시"}
    assert all("open_authority_code" not in option for option in options)


def test_mois_city_summary_filters_out_other_cities_in_the_same_province(monkeypatch):
    # OPN_ATMY_GRP_CD only resolves to a province-level group (verified from
    # the live feed — see MOIS_TOURISM_BUSINESS_REGIONS), so a single page for
    # 경주시's code also contains businesses from other 경상북도 cities. The
    # summary must only count rows whose address is actually in 경주시.
    async def fake_fetch(operation, open_authority_code, base_date, page_no, num_rows):
        return {"response": {"body": {"totalCount": 2, "items": {"item": [
            {"ROAD_NM_ADDR": "경상북도 경주시 태종로685번길 6", "SALS_STTS_NM": "영업/정상", "CULTR_SPTS_TPBIZ_NM": "관광숙박업"},
            {"ROAD_NM_ADDR": "경상북도 포항시 중앙로 1", "SALS_STTS_NM": "영업/정상", "CULTR_SPTS_TPBIZ_NM": "관광숙박업"},
        ]}}}}

    monkeypatch.setattr("app.data_sources.fetch_mois_tourism_business", fake_fetch)
    result = asyncio.run(fetch_mois_city_business_summary("47130", "info"))
    assert result["province_group_record_count"] == 2
    assert result["raw_record_count"] == 1  # only the 경주시 row
    assert result["operating_tourism_accommodation_business_count"] == 1
    assert result["low_sample"] is True  # province group has well under 20 records
