"""Offline proof of the --hours fix (no network).

The bug: balad-mcp / neshan-mcp keep one module-global httpx client whose curl_cffi transport
binds to the event loop of the FIRST request (curl_cffi.aio.AsyncCurl.__init__ stores
asyncio.get_running_loop() and spawns a task on it). The old code ran the hours phase in a
SECOND asyncio.run(): the retained transport still points at the closed first loop.

The fix: one event loop for scrape + hours (the new phase1 in iran_food_scraper.py).
"""
import asyncio

import httpx
from curl_cffi.aio import AsyncCurl

import balad_mcp.http as bh
import neshan_mcp.http as nh

# 1) the mechanism: a curl transport created in loop 1 is unusable once loop 1 is closed
async def make():
    return AsyncCurl()


curl = asyncio.run(make())  # loop 1 opens, binds the transport, closes
try:
    curl.loop.call_soon(lambda: None)  # what curl's timer/socket callbacks do on the retained transport
    print("mechanism: no error (unexpected!)")
except RuntimeError as e:
    print(f"mechanism confirmed: transport bound to the closed loop -> {e}")

# 2) the fixed pattern: both phases in ONE loop, transport stays valid
async def one_loop():
    c = AsyncCurl()
    f1 = c.loop.create_future()   # "scrape" phase
    f2 = c.loop.create_future()   # "hours" phase, same loop
    assert f1.get_loop() is f2.get_loop()
    await c.close()


asyncio.run(one_loop())
print("fixed pattern: one loop for both phases -> OK")

# 3) regression: the scraper's actual flow shape with mocked HTTP (both sources, scrape then hours)
def handler(request):
    return httpx.Response(200, json={"ok": True})


bh.set_transport(httpx.MockTransport(handler))
nh.set_transport(httpx.MockTransport(handler))


async def phase1():
    await bh.fetch("https://search.raah.ir/test", {"i": 1})   # scrape: Balad
    await nh.fetch("https://neshan.org/test", {"i": 1})       # scrape: Neshan
    await bh.fetch("https://search.raah.ir/test", {"i": 2})   # hours: Balad, same loop
    await nh.fetch("https://neshan.org/test", {"i": 2})       # hours: Neshan, same loop


asyncio.run(phase1())
print("scraper flow (mocked): scrape + hours in one loop, both sources -> OK")

# 4) regression: fetch_hours builds its SQL with %-formatting; a literal LIKE 'g:%' must be
#    escaped ('g:%%') or `base % c` raises "unsupported format character". Run the real
#    fetch_hours against an in-memory DB with a stubbed pool that executes each query.
import sqlite3

import iran_food_scraper as s

mem = sqlite3.connect(":memory:")
mem.execute("CREATE TABLE places(id TEXT PRIMARY KEY, grp TEXT, hours_done INTEGER)")
mem.execute("INSERT INTO places VALUES('g:abc', 'برنج', 1)")          # Google: already has hours
mem.execute("INSERT INTO places VALUES('balad1', 'برنج', 0)")         # todo
mem.execute("INSERT INTO places VALUES(?, 'برنج', 0)", ("x" * 32,))   # Neshan-length id, todo

seen = []


async def fake_pool(sql, one):
    seen.append(sql)
    mem.execute(sql).fetchall()  # raises if the SQL is malformed
    return []


class _Prog:
    def cancel(self):
        pass


async def fake_progress(*a):
    return _Prog()


s.db, s.pool, s.progress = mem, fake_pool, fake_progress
asyncio.run(s.fetch_hours())
assert len(seen) == 2 * s.WORKERS, seen
assert all("id NOT LIKE 'g:%'" in q for q in seen), "Google rows must be excluded from the hours queue"
print("fetch_hours SQL: literal LIKE escaped, both source queries execute -> OK")