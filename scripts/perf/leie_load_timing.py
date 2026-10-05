"""Micro-measurement: cost of the OIG LEIE CSV load (download + index) that
`OIGLEIEConnector.lookup_by_npi` triggers on first use -- once, and when 16
tasks hit a cold cache at the same time (the pipeline's first gather wave)."""
import asyncio, os, sys, time
os.environ.setdefault("SECRET_KEY", "b" * 64); os.environ.setdefault("ALLOWED_HOSTS", "*")
import psutil
from app.Tefca import connectors as c

def rss(): return psutil.Process().memory_info().rss / 1e6

async def cold(label, n):
    c._LEIE_CACHE.update(loaded_at=0.0, by_npi={}, by_name={}, row_count=0)
    peak = [rss()]
    async def sample():
        while True:
            peak[0] = max(peak[0], rss()); await asyncio.sleep(0.2)
    s = asyncio.create_task(sample())
    r0 = rss(); t0 = time.perf_counter()
    ok = await asyncio.gather(*(c._ensure_leie_loaded() for _ in range(n)))
    dt = time.perf_counter() - t0; s.cancel()
    print(f"{label}: n={n} ok={all(ok)} seconds={dt:.1f} rows={c._LEIE_CACHE['row_count']} "
          f"rss_before={r0:.0f}MB rss_after={rss():.0f}MB peak={peak[0]:.0f}MB")

async def main():
    await cold("single cold load", 1)
    t0 = time.perf_counter(); await c._ensure_leie_loaded(); print(f"warm call: {time.perf_counter()-t0:.4f}s")
    await cold("16 concurrent cold loads", 16)

asyncio.run(main())
