from app.data_sources import (compute_hub_spatial_spread, compute_visitor_stability,
                              summarize_attraction_concentration,
                              summarize_mois_tourism_business,
                              mois_tourism_business_regions,
                              _aggregate_national_visitors, select_national_peers, is_city_level,
                              build_live_visitor_snapshot)


def _visitor_item(code: str, name: str, category: str, count: str) -> dict:
    return {"signguCode": code, "signguNm": name, "touDivNm": category, "touNum": count,
            "baseYmd": "20260701", "daywkDivCd": "4", "daywkDivNm": "목요일"}


def test_national_ranking_selects_top_demand_peers_excluding_target():
    # 5 municipalities with distinct outside-visitor counts; the target (C)
    # sits in the middle, so its national peers should be the two regions
    # ranked above it by demand, not simply "the other 4 cities".
    items = [
        _visitor_item("A", "가군", "외지인(b)", "500"),
        _visitor_item("B", "나군", "외지인(b)", "400"),
        _visitor_item("C", "다군", "외지인(b)", "300"),
        _visitor_item("D", "라군", "외지인(b)", "200"),
        _visitor_item("E", "마군", "외지인(b)", "100"),
    ]
    by_area = _aggregate_national_visitors(items)
    assert len(by_area) == 5
    ranking = sorted(by_area.values(), key=lambda entry: -entry["outside_visitors"])
    peers = select_national_peers(ranking, exclude_area_cd="C", count=2)
    assert [peer["area_cd"] for peer in peers] == ["A", "B"]
    # The excluded target itself must never appear among its own peers.
    assert all(peer["area_cd"] != "C" for peer in peers)


def test_is_city_level_excludes_both_kinds_of_gu():
    assert is_city_level("경주시") is True
    assert is_city_level("완주군") is True
    assert is_city_level("세종특별자치시") is True
    assert is_city_level("강남구") is False  # standalone metro-city district
    assert is_city_level("수원시 팔달구") is False  # sub-city district of a 시


def test_national_peers_never_include_a_gu_even_if_it_outranks_every_si():
    # A 구 (강남구) has far higher demand than any 시/군 here, but a 시 target
    # should only ever be benchmarked against other 시/군 — comparing a city
    # to a district isn't the same administrative unit.
    items = [
        _visitor_item("11680", "강남구", "외지인(b)", "900"),
        _visitor_item("41110", "수원시", "외지인(b)", "500"),
        _visitor_item("47130", "경주시", "외지인(b)", "300"),
        _visitor_item("51150", "강릉시", "외지인(b)", "200"),
    ]
    by_area = _aggregate_national_visitors(items)
    ranking = sorted(by_area.values(), key=lambda entry: -entry["outside_visitors"])
    peers = select_national_peers(ranking, exclude_area_cd="47130", count=3)
    assert [peer["area_cd"] for peer in peers] == ["41110", "51150"]
    assert all(is_city_level(str(peer["area_name"])) for peer in peers)


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
