"""Quick look at what Balad's search returns for city names (run by the geocode-test workflow)."""
import asyncio, json
from balad_mcp.maps import bl_search

sh = json.load(open("data/shahr.json", encoding="utf-8"))
os_ = {o["id"]: o["name"] for o in json.load(open("data/ostan.json", encoding="utf-8"))}
want = ["ساوه", "قلعه نو", "حسن آباد", "فیروزآباد", "آب بر", "بم", "اسلام آباد غرب", "زرین دشت", "خوانسار", "مهران"]
pick = [next(x for x in sh if x["name"] == n) for n in want if any(x["name"] == n for x in sh)]
async def main():
    for x in pick:
        q = f'{x["name"]} {os_[x["ostan"]]}'
        try:
            r = await bl_search(query=q, limit=4)
            print("Q:", q)
            for row in r["results"]:
                print("   ", row.get("type"), "|", row.get("name"), "|", row.get("address"), "|", row.get("lat"), row.get("lng"))
        except Exception as e:
            print("Q:", q, "FAILED", type(e).__name__, str(e)[:120])
        await asyncio.sleep(2)
asyncio.run(main())
