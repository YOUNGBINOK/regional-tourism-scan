from app.data_sources import compute_visitor_stability, summarize_attraction_concentration


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
