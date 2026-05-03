"""
Load test: 10 requests/sec de análisis durante 2 minutos.

Uso rápido (sin Locust, solo asyncio + httpx):
    python scripts/load_test_analysis.py

Configuración:
    BASE_URL  — URL del servidor (default http://localhost:8000)
    RPS       — requests por segundo (default 10)
    DURATION  — duración en segundos (default 120)
"""

import asyncio
import os
import random
import statistics
import time

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
RPS = int(os.getenv("RPS", "10"))
DURATION = int(os.getenv("DURATION", "120"))

# Payloads variados para simular tráfico realista
SAMPLE_PAYLOADS = [
    {"keyword": "iPhone 15 Pro Max 256GB", "cost_price": 800.0},
    {"keyword": "Nintendo Switch OLED", "cost_price": 250.0},
    {"keyword": "PS5 Disc Edition", "cost_price": 400.0},
    {"keyword": "AirPods Pro 2nd Gen", "cost_price": 180.0},
    {"keyword": "MacBook Air M2", "cost_price": 900.0},
    {"keyword": "Dyson V15 Detect", "cost_price": 500.0},
    {"keyword": "Lego Star Wars UCS", "cost_price": 350.0},
    {"keyword": "Nike Air Jordan 1 Retro", "cost_price": 170.0},
    {"keyword": "Canon EOS R6 Mark II", "cost_price": 1800.0},
    {"keyword": "Samsung Galaxy S24 Ultra", "cost_price": 950.0},
    {"barcode": "194253397113", "cost_price": 700.0},
    {"barcode": "045496883386", "cost_price": 260.0},
    {"keyword": "KitchenAid Stand Mixer", "cost_price": 280.0},
    {"keyword": "Bose QuietComfort Ultra", "cost_price": 300.0},
    {"keyword": "iPad Pro 12.9 M2", "cost_price": 850.0},
]

# ---------- métricas ----------

results: list[dict] = []


def random_payload() -> dict:
    base = random.choice(SAMPLE_PAYLOADS).copy()
    base["marketplace"] = random.choice(["ebay", "ebay", "ebay", "amazon_fba"])
    return base


async def send_request(client: httpx.AsyncClient, req_id: int) -> None:
    payload = random_payload()
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{BASE_URL}/api/v1/analysis/",
            json=payload,
            timeout=60.0,
        )
        elapsed = time.perf_counter() - start
        results.append({
            "id": req_id,
            "status": resp.status_code,
            "elapsed": elapsed,
            "payload": payload.get("keyword") or payload.get("barcode"),
        })
    except Exception as e:
        elapsed = time.perf_counter() - start
        results.append({
            "id": req_id,
            "status": "error",
            "elapsed": elapsed,
            "error": str(e),
            "payload": payload.get("keyword") or payload.get("barcode"),
        })


async def main() -> None:
    print(f"🔥 Load test: {RPS} req/s × {DURATION}s = {RPS * DURATION} requests totales")
    print(f"   Target: {BASE_URL}/api/v1/analysis/")
    print(f"   Iniciando...\n")

    # httpx con pool grande para soportar concurrencia
    limits = httpx.Limits(
        max_connections=RPS * 10,
        max_keepalive_connections=RPS * 5,
    )
    async with httpx.AsyncClient(limits=limits) as client:
        req_id = 0
        start_time = time.perf_counter()

        while (time.perf_counter() - start_time) < DURATION:
            tick_start = time.perf_counter()

            # Lanzar RPS requests en paralelo
            tasks = []
            for _ in range(RPS):
                req_id += 1
                tasks.append(asyncio.create_task(send_request(client, req_id)))

            # No esperamos a que terminen — solo esperamos al siguiente tick
            await asyncio.sleep(max(0, 1.0 - (time.perf_counter() - tick_start)))

            # Progreso cada 10 segundos
            elapsed_total = time.perf_counter() - start_time
            if int(elapsed_total) % 10 == 0 and int(elapsed_total) > 0:
                completed = len([r for r in results if r])
                errors = len([r for r in results if r.get("status") == "error" or (isinstance(r.get("status"), int) and r["status"] >= 500)])
                print(f"   [{int(elapsed_total):>3}s] enviadas={req_id}  completadas={completed}  errores={errors}")

        # Esperar a que terminen las requests pendientes (máx 60s)
        print(f"\n⏳ Esperando requests pendientes (max 60s)...")
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=60.0)

    print_report()


def print_report() -> None:
    if not results:
        print("❌ No se completaron requests")
        return

    total = len(results)
    success = [r for r in results if isinstance(r.get("status"), int) and 200 <= r["status"] < 400]
    errors_4xx = [r for r in results if isinstance(r.get("status"), int) and 400 <= r["status"] < 500]
    errors_5xx = [r for r in results if isinstance(r.get("status"), int) and r["status"] >= 500]
    conn_errors = [r for r in results if r.get("status") == "error"]
    rate_limited = [r for r in results if isinstance(r.get("status"), int) and r["status"] == 403]

    times = [r["elapsed"] for r in results if isinstance(r.get("status"), int)]

    print("\n" + "=" * 60)
    print("📊 RESULTADOS DEL LOAD TEST")
    print("=" * 60)
    print(f"  Total requests completadas: {total}")
    print(f"  ✅ Exitosas (2xx/3xx):      {len(success)}")
    print(f"  ⚠️  Rate limited (403):      {len(rate_limited)}")
    print(f"  ❌ Client errors (4xx):      {len(errors_4xx)}")
    print(f"  💥 Server errors (5xx):      {len(errors_5xx)}")
    print(f"  🔌 Connection errors:        {len(conn_errors)}")

    if times:
        print(f"\n⏱️  Latencia (requests completadas):")
        print(f"  Min:    {min(times):>8.2f}s")
        print(f"  Max:    {max(times):>8.2f}s")
        print(f"  Mean:   {statistics.mean(times):>8.2f}s")
        print(f"  Median: {statistics.median(times):>8.2f}s")
        print(f"  P95:    {sorted(times)[int(len(times) * 0.95)]:>8.2f}s")
        print(f"  P99:    {sorted(times)[int(len(times) * 0.99)]:>8.2f}s")

    if success:
        success_times = [r["elapsed"] for r in success]
        print(f"\n⏱️  Latencia (solo exitosas):")
        print(f"  Mean:   {statistics.mean(success_times):>8.2f}s")
        print(f"  P95:    {sorted(success_times)[int(len(success_times) * 0.95)]:>8.2f}s")

    if conn_errors:
        print(f"\n🔌 Ejemplos de errores de conexión:")
        for e in conn_errors[:5]:
            print(f"  req#{e['id']}: {e.get('error', 'unknown')[:80]}")

    error_rate = (len(errors_5xx) + len(conn_errors)) / total * 100 if total else 0
    print(f"\n📈 Error rate (5xx + conn): {error_rate:.1f}%")
    print(f"   Throughput efectivo:     {len(success) / DURATION:.1f} req/s" if success else "")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
