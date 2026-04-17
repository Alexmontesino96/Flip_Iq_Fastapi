"""Test de consistencia: 3 análisis del mismo producto."""
import asyncio
import time

import httpx

API = "http://localhost:8000"
KEYWORD = "AirPods Pro"
COST = 150.0
CONDITION = "new"
RUNS = 3


async def run_analysis(run_num):
    print(f"\n--- Run {run_num} ---")
    t0 = time.time()
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{API}/api/v1/analysis/",
            json={
                "keyword": KEYWORD,
                "cost_price": COST,
                "condition": CONDITION,
                "marketplace": "ebay",
            },
        )
        elapsed = time.time() - t0
        if r.status_code != 200:
            print(f"  ERROR {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()

    ebay = data.get("ebay_analysis") or {}
    comps = ebay.get("comps") or {}
    confidence_obj = ebay.get("confidence") or {}
    max_buy_obj = ebay.get("max_buy_price") or {}
    profit_obj = ebay.get("profit_detail") or {}
    velocity_obj = ebay.get("velocity") or {}
    risk_obj = ebay.get("risk") or {}
    summary = data.get("summary") or {}
    warnings = summary.get("warnings", [])

    result = {
        "run": run_num,
        "time_s": round(elapsed, 1),
        "ebay_comps": comps.get("total_sold", 0),
        "ebay_median": comps.get("median_price", 0),
        "ebay_min": comps.get("min_price", 0),
        "ebay_max": comps.get("max_price", 0),
        "distribution": comps.get("distribution_shape", "?"),
        "flip_score": ebay.get("flip_score") or data.get("flip_score") or 0,
        "recommendation": data.get("recommendation", "?"),
        "confidence": confidence_obj.get("score", 0),
        "max_buy": max_buy_obj.get("recommended_max", 0),
        "profit": profit_obj.get("profit", 0),
        "roi": profit_obj.get("roi", 0),
        "velocity": velocity_obj.get("score", 0),
        "risk": risk_obj.get("score", 0),
        "warnings": warnings,
    }

    rng = f"${result['ebay_min']:.0f}-${result['ebay_max']:.0f}"
    print(f"  Time: {result['time_s']}s | Comps: {result['ebay_comps']} | Median: ${result['ebay_median']:.2f} | Range: {rng} | Dist: {result['distribution']}")
    print(f"  Flip: {result['flip_score']} | Rec: {result['recommendation']} | Conf: {result['confidence']} | Risk: {result['risk']} | Velocity: {result['velocity']}")
    print(f"  Max buy: ${result['max_buy']:.2f} | Profit: ${result['profit']:.2f} | ROI: {result['roi']:.1%}")
    print(f"  Warnings ({len(warnings)}):")
    for w in warnings:
        print(f"    - {w[:100]}")

    return result


async def main():
    print(f"=== CONSISTENCY TEST (SCRAPER): {KEYWORD} x{RUNS} ===")
    print(f"Cost: ${COST}, Condition: {CONDITION}")

    results = []
    for i in range(1, RUNS + 1):
        r = await run_analysis(i)
        if r:
            results.append(r)
        if i < RUNS:
            print("\n  (waiting 5s...)")
            await asyncio.sleep(5)

    if len(results) < 2:
        print("\nNot enough results to compare.")
        return

    print("\n" + "=" * 80)
    print("VARIANCE ANALYSIS")
    print("=" * 80)

    metrics = [
        ("ebay_comps", "Clean comps"),
        ("ebay_median", "Median ($)"),
        ("flip_score", "Flip Score"),
        ("confidence", "Confidence"),
        ("risk", "Risk"),
        ("velocity", "Velocity"),
        ("max_buy", "Max Buy ($)"),
        ("profit", "Profit ($)"),
    ]

    header = f"  {'Metric':<15}"
    for r in results:
        header += f"  {'Run '+str(r['run']):>10}"
    header += f"  {'MaxDev%':>10}"
    print(header)
    print("  " + "-" * (15 + 12 * (len(results) + 1)))

    for key, label in metrics:
        vals = [r[key] for r in results]
        avg = sum(vals) / len(vals) if vals else 0
        if avg > 0:
            max_dev = max(abs(v - avg) / avg * 100 for v in vals)
        else:
            max_dev = 0

        row = f"  {label:<15}"
        for v in vals:
            if isinstance(v, float):
                row += f"  {v:>10.2f}"
            else:
                row += f"  {v:>10}"
        row += f"  {max_dev:>9.1f}%"
        print(row)

    medians = [r["ebay_median"] for r in results]
    avg_median = sum(medians) / len(medians) if medians else 0
    median_var = max(abs(m - avg_median) / avg_median * 100 for m in medians) if avg_median > 0 else 0

    print(f"\n  Median max deviation: {median_var:.1f}% (target: <10%)")
    if median_var < 10:
        print("  VERDICT: GOOD consistency")
    elif median_var < 25:
        print("  VERDICT: MODERATE variance")
    else:
        print("  VERDICT: HIGH variance — systemic issue")


asyncio.run(main())
