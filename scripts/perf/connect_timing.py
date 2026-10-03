"""Micro-measurement: how long does opening a DB connection take on this host,
sequentially and 16-at-once, under NullPool vs QueuePool? Isolates 'physical
connect cost' from everything else in the pipeline."""
import asyncio, os, statistics, sys, time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

URL = os.environ["DATABASE_URL"]

async def one(engine):
    t0 = time.perf_counter()
    async with engine.connect() as c:
        await c.execute(text("select 1"))
    return time.perf_counter() - t0

async def run(label, engine, n_seq=10, n_conc=16, rounds=3):
    seq = [await one(engine) for _ in range(n_seq)]
    conc = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        r = await asyncio.gather(*(one(engine) for _ in range(n_conc)))
        conc.append((time.perf_counter() - t0, max(r), statistics.mean(r)))
    print(f"{label}: sequential connect+select1 mean={statistics.mean(seq)*1000:.0f}ms max={max(seq)*1000:.0f}ms | "
          f"16-concurrent rounds wall={[round(c[0],2) for c in conc]} s, per-task max={[round(c[1],2) for c in conc]} s")
    await engine.dispose()

async def main():
    await run("NullPool", create_async_engine(URL, poolclass=NullPool))
    await run("QueuePool(5+10)", create_async_engine(URL, pool_size=5, max_overflow=10, pool_pre_ping=True))
    await run("QueuePool(20+0)", create_async_engine(URL, pool_size=20, max_overflow=0, pool_pre_ping=True))

asyncio.run(main())
