from app.data_sources import (compute_hub_spatial_spread, compute_visitor_stability,
                              summarize_attraction_concentration,
                              summarize_mois_tourism_business,
                              mois_tourism_business_regions,
                              _aggregate_national_visitors, _percentile_rank, _quantile,
                              classify_admin_type, is_independent_municipality,
                              is_capital_region, resolve_region_province, build_peer_group,
                              build_live_visitor_snapshot)


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


def test_build_peer_group_never_mixes_admin_types():
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
    group = build_peer_group(ranking, "47130", "경주시", count=3)
    assert group["admin_type"] == "시"
    peer_cds = [peer["area_cd"] for peer in group["peers"]]
    assert "11680" not in peer_cds  # the 자치구 must never appear as a 시's peer
    assert set(peer_cds) == {"51150", "52130"}


def test_build_peer_group_picks_closest_demand_scale_not_simply_the_top():
    # Five 시 with descending demand; the target (C, rank 3) should be
    # benchmarked against the two *closest* in demand scale (B and D), not
    # simply "the other top performers nationally" (A and B).
    items = [
        _visitor_item("10", "가시", "외지인(b)", "500"),
        _visitor_item("20", "나시", "외지인(b)", "400"),
        _visitor_item("30", "다시", "외지인(b)", "300"),
        _visitor_item("40", "라시", "외지인(b)", "200"),
        _visitor_item("50", "마시", "외지인(b)", "100"),
    ]
    ranking = _ranking(items)
    group = build_peer_group(ranking, "30", "다시", count=2)
    assert [peer["area_cd"] for peer in group["peers"]] == ["20", "40"]


def test_capital_region_lookup_for_a_known_gyeonggi_and_non_capital_city():
    assert is_capital_region(resolve_region_province("41110", "수원시")) is True
    assert is_capital_region(resolve_region_province("47130", "경주시")) is False


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
