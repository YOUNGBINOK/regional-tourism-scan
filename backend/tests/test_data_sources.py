from app.data_sources import (compute_hub_spatial_spread, compute_visitor_stability,
                              summarize_attraction_concentration,
                              summarize_mois_tourism_business)


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
