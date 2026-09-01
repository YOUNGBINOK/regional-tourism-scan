"""National batch diagnosis snapshot (AGENTS.md §7 item 5).

Runs the same peer-comparison pipeline the live site uses
(fetch_national_visitor_ranking_window → build_peer_group →
lightweight stay/spend/lodging/dispersion peer axes) across every
independent municipality (시/군/자치구, 일반구 제외), and writes the
result to a JSON file so the "전국 시군구 전수진단" deliverable exists
as a real, inspectable artifact instead of a claim.

Costs real KTO Data Lab quota: ~4 calls per municipality for its own
peer-axis snapshot, batched with asyncio.gather in chunks to stay
polite to the API, plus the shared national ranking scan. Running this
against all ~229 independent municipalities will burn a meaningful
share of the ~1,000/day per-service quota — this is exactly the
"~40 calls per single diagnosis" quota-risk weakness AGENTS.md §7 item 5
flags, at a fixed one-time cost, run manually rather than on page load.

Usage:
    cd backend && python -m scripts.national_diagnosis_snapshot \\
        --base-ymd 20260701 --limit 40 --out national_diagnosis.json

--limit caps how many municipalities to process in one run (KTO's daily
quota is shared across every endpoint this app calls, including normal
site traffic on the same key) — run it in batches across days rather
than attempting all ~229 in one sitting, and pass --resume-from to
skip municipalities an earlier run already wrote to --out.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_sources import (  # noqa: E402
    build_peer_group, compute_hub_spatial_spread, fetch_kto_catalog_service_by_path,
    fetch_municipal_hub_attractions, fetch_national_visitor_ranking_window,
    is_independent_municipality, _json_envelope_items,
)


def _first_metric(response: object, value_key: str, area_cd: str) -> float | None:
    if not isinstance(response, dict):
        return None
    target_codes = {"52111", "52113"} if area_cd == "52110" else {area_cd}
    values = []
    for record in _json_envelope_items(response):
        if record.get("signguCd") not in target_codes or record.get(value_key) is None:
            continue
        try:
            values.append(float(record[value_key]))
        except (TypeError, ValueError):
            continue
    return round(sum(values) / len(values), 2) if values else None


_axis_memo: dict[str, dict[str, float | None] | None] = {}


async def _axis_snapshot(area_cd: str, base_ym: str) -> dict[str, float | None] | None:
    # Peers recur heavily across targets in the same demand bracket (the
    # same handful of mid-sized 시 end up as everyone's Peer Group), so this
    # memoizes within a single run — without it, a 20-target run could
    # re-fetch the same popular peer a dozen times, burning quota that
    # should go toward reaching more distinct municipalities instead.
    memo_key = f"{area_cd}:{base_ym}"
    if memo_key in _axis_memo:
        return _axis_memo[memo_key]
    result = await _axis_snapshot_uncached(area_cd, base_ym)
    _axis_memo[memo_key] = result
    return result


async def _axis_snapshot_uncached(area_cd: str, base_ym: str) -> dict[str, float | None] | None:
    try:
        region_params = {"areaCd": area_cd[:2], "baseYm": base_ym}
        if area_cd != "52110":
            region_params["signguCd"] = area_cd
        stay, spend, lodging, hubs = await asyncio.gather(
            fetch_kto_catalog_service_by_path("AreaTarDemDsService", "areaTarSjrnDsList",
                                              {**region_params, "tarSjrnDsIxCd": "21", "numOfRows": "1000", "pageNo": "1", "_type": "json"}),
            fetch_kto_catalog_service_by_path("AreaTarDemDsService", "areaTarExpDsList",
                                              {**region_params, "tarExpDsIxCd": "22", "numOfRows": "1000", "pageNo": "1", "_type": "json"}),
            fetch_kto_catalog_service_by_path("AreaTarDemDsService", "areaTarSjrnDsList",
                                              {**region_params, "tarSjrnDsIxCd": "2102", "numOfRows": "1000", "pageNo": "1", "_type": "json"}),
            fetch_municipal_hub_attractions(area_cd, base_ym),
        )
        hub_spread = compute_hub_spatial_spread(hubs) if hubs else None
        return {
            "stay_intensity": _first_metric(stay, "tarSjrnDsIxVal", area_cd),
            "spend_intensity": _first_metric(spend, "tarExpDsIxVal", area_cd),
            "lodging_share_index": _first_metric(lodging, "tarSjrnDsIxVal", area_cd),
            "dispersion_spread_km": hub_spread["spread_km"] if hub_spread else None,
        }
    except Exception as error:
        return {"error": str(error)}


async def run(base_ymd: str, limit: int, resume_from: set[str]) -> list[dict]:
    ranking = await fetch_national_visitor_ranking_window(base_ymd, days=7)
    independent = [entry for entry in ranking if is_independent_municipality(str(entry["area_name"]))]
    targets = [entry for entry in independent if str(entry["area_cd"]) not in resume_from][:limit]
    base_ym = base_ymd[:6]

    results = []
    for entry in targets:
        area_cd, area_name = str(entry["area_cd"]), str(entry["area_name"])
        group = await build_peer_group(independent, area_cd, area_name, count=6, base_ym=base_ym)
        target_axes = await _axis_snapshot(area_cd, base_ym)
        peer_axes = await asyncio.gather(*[_axis_snapshot(str(p["area_cd"]), base_ym) for p in group["peers"]])
        results.append({
            "area_cd": area_cd, "area_name": area_name,
            "national_percentile": entry["percentile"], "outside_visitors_7d_avg": entry["outside_visitors"],
            "admin_type": group["admin_type"], "capital_region": group["capital_region"],
            "criteria_note": group["criteria_note"],
            "target_axes": target_axes,
            "peers": [{"area_cd": p["area_cd"], "area_name": p["area_name"], "axes": axes}
                     for p, axes in zip(group["peers"], peer_axes)],
        })
        print(f"done: {area_name} ({area_cd})", file=sys.stderr)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ymd", required=True, help="YYYYMMDD, end date of the 7-day demand window")
    parser.add_argument("--limit", type=int, default=20, help="max municipalities to process this run")
    parser.add_argument("--out", default="national_diagnosis.json")
    args = parser.parse_args()

    out_path = Path(args.out)
    existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    resume_from = {row["area_cd"] for row in existing}

    new_results = asyncio.run(run(args.base_ymd, args.limit, resume_from))
    out_path.write_text(json.dumps(existing + new_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(existing) + len(new_results)} total municipalities to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
