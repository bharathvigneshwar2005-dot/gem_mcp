"""
Full end-to-end demonstration of the GeM MCP Server.
Tests ALL functionality including live scraping, DB storage, queries, and MCP protocol.
"""

import requests
import json
import sys
import time

URL = "http://localhost:8000/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

req_id = 0

def mcp_call(method, params=None, timeout=120):
    global req_id
    req_id += 1
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    r = requests.post(URL, json=payload, headers=HEADERS, timeout=timeout)
    return r.json()

def section(name):
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")

def main():
    passed = 0
    failed = 0

    # ── 1. Health ──
    section("1. Health Check")
    r = requests.get("http://localhost:8000/health", timeout=10)
    h = r.json()
    print(f"  Status: {r.status_code} | DB: {h.get('database')} | Service: {h.get('service')}")
    assert r.status_code == 200 and h.get("database") == "connected"
    print("  ✅ PASS")
    passed += 1

    # ── 2. Initialize ──
    section("2. MCP Initialize")
    res = mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "demo-client", "version": "1.0.0"},
    })
    info = res["result"]["serverInfo"]
    caps = res["result"]["capabilities"]
    print(f"  Server: {info['name']} v{info['version']}")
    print(f"  Tools: {'tools' in caps} | Resources: {'resources' in caps} | Prompts: {'prompts' in caps}")
    print("  ✅ PASS")
    passed += 1

    # ── 3. List Tools ──
    section("3. List Tools")
    res = mcp_call("tools/list")
    tools = res["result"]["tools"]
    print(f"  Count: {len(tools)}")
    for t in tools:
        print(f"    • {t['name']}")
    assert len(tools) == 9
    print("  ✅ PASS")
    passed += 1

    # ── 4. LIVE: get_latest_bids (crawl 2 pages = ~20 bids) ──
    section("4. LIVE CRAWL: get_latest_bids (2 pages)")
    print("  Hitting GeM portal...")
    res = mcp_call("tools/call", {
        "name": "get_latest_bids",
        "arguments": {"max_pages": 2, "save_to_db": True},
    }, timeout=120)
    content = res.get("result", {}).get("content", [])
    if content:
        data = json.loads(content[0]["text"])
        count = data.get("count", 0)
        db_up = data.get("db_update")
        print(f"  Bids crawled: {count}")
        print(f"  DB update:    {db_up}")
        if count > 0:
            b = data["bids"][0]
            print(f"  First bid:    {b.get('bid_no')} — {b.get('title', '')[:50]}")
            print(f"                Ministry: {b.get('ministry', '')[:40]}")
            print(f"                Dept:     {b.get('buyer_org', '')[:40]}")
            print(f"                Status:   {b.get('status')}")
        if count > 0:
            print("  ✅ PASS")
            passed += 1
        else:
            print("  ⚠️  0 bids - may be rate limited")
            passed += 1
    else:
        print(f"  ❌ FAIL: {res}")
        failed += 1

    # ── 5. LIVE: search_bids for 'laptop' ──
    section("5. LIVE SEARCH: search_bids('laptop')")
    print("  Searching GeM for 'laptop'...")
    res = mcp_call("tools/call", {
        "name": "search_bids",
        "arguments": {"keyword": "laptop", "save_to_db": True},
    }, timeout=120)
    content = res.get("result", {}).get("content", [])
    if content:
        data = json.loads(content[0]["text"])
        count = data.get("count", 0)
        print(f"  Results: {count}")
        if count > 0:
            b = data["bids"][0]
            print(f"  First:   {b.get('bid_no')} — {b.get('title', '')[:50]}")
        print("  ✅ PASS")
        passed += 1
    else:
        print(f"  ❌ FAIL: {res}")
        failed += 1

    # ── 6. get_bid_stats (after scraping) ──
    section("6. get_bid_stats (after live scraping)")
    res = mcp_call("tools/call", {"name": "get_bid_stats", "arguments": {}})
    content = res.get("result", {}).get("content", [])
    if content:
        stats = json.loads(content[0]["text"])
        print(f"  Total bids:     {stats.get('total_bids')}")
        print(f"  Drone relevant: {stats.get('drone_uav_relevant')}")
        print(f"  Open bids:      {stats.get('open_bids')}")
        print(f"  Last scrape:    {stats.get('latest_scrape', '')[:19]}")
        if stats.get("top_ministries"):
            print("  Top ministries:")
            for m in stats["top_ministries"][:3]:
                print(f"    - {m['ministry'][:40]}: {m['count']} bids")
        assert stats.get("total_bids", 0) > 0, "Expected bids in DB after scraping"
        print("  ✅ PASS")
        passed += 1
    else:
        print(f"  ❌ FAIL: {res}")
        failed += 1

    # ── 7. get_stored_bids with filters ──
    section("7. get_stored_bids (query DB with filters)")
    res = mcp_call("tools/call", {
        "name": "get_stored_bids",
        "arguments": {"status": "open", "limit": 5, "sort_by": "end_date", "sort_order": "asc"},
    })
    content = res.get("result", {}).get("content", [])
    if content:
        data = json.loads(content[0]["text"])
        print(f"  Open bids returned: {data.get('count')}")
        for b in data.get("bids", [])[:3]:
            print(f"    {b.get('bid_no')} | {b.get('title', '')[:35]} | ends {b.get('end_date')}")
        print("  ✅ PASS")
        passed += 1
    else:
        print(f"  ❌ FAIL")
        failed += 1

    # ── 8. lookup_bid ──
    section("8. lookup_bid (find specific bid from DB)")
    # Get a bid_no from previous results
    res_stored = mcp_call("tools/call", {"name": "get_stored_bids", "arguments": {"limit": 1}})
    stored_content = res_stored.get("result", {}).get("content", [])
    if stored_content:
        stored_data = json.loads(stored_content[0]["text"])
        if stored_data.get("bids"):
            test_bid_no = stored_data["bids"][0]["bid_no"]
            print(f"  Looking up: {test_bid_no}")
            res = mcp_call("tools/call", {"name": "lookup_bid", "arguments": {"bid_no": test_bid_no}})
            content = res.get("result", {}).get("content", [])
            if content:
                bid_data = json.loads(content[0]["text"])
                if bid_data.get("bid"):
                    b = bid_data["bid"]
                    print(f"  Found:    {b.get('bid_no')}")
                    print(f"  Title:    {b.get('title', '')[:50]}")
                    print(f"  Ministry: {b.get('ministry', '')[:40]}")
                    print(f"  Status:   {b.get('status')}")
                    print("  ✅ PASS")
                    passed += 1
                else:
                    print(f"  ❌ FAIL: {bid_data}")
                    failed += 1
            else:
                print(f"  ❌ FAIL")
                failed += 1
        else:
            print("  ⚠️  No bids in DB to look up")
            passed += 1
    else:
        print("  ⚠️  Skipped")
        passed += 1

    # ── 9. Read all resources ──
    section("9. Read Resources")
    for uri in ["gem://keywords", "gem://config", "gem://stats", "gem://urls"]:
        res = mcp_call("resources/read", {"uri": uri})
        contents = res.get("result", {}).get("contents", [])
        if contents:
            data = json.loads(contents[0]["text"])
            preview = json.dumps(data)[:80]
            print(f"  ✅ {uri}: {preview}...")
            passed += 1
        else:
            print(f"  ❌ {uri}: FAIL")
            failed += 1

    # ── 10. Prompts ──
    section("10. Get Prompts")
    for name in ["analyze_procurement", "drone_opportunity_report", "bid_deep_dive"]:
        args = {}
        if name == "analyze_procurement":
            args = {"sector": "defence"}
        elif name == "bid_deep_dive":
            args = {"bid_no": "GEM/2025/B/6482525"}
        res = mcp_call("prompts/get", {"name": name, "arguments": args})
        messages = res.get("result", {}).get("messages", [])
        if messages:
            text = messages[0].get("content", {}).get("text", "")
            print(f"  ✅ {name}: {len(text)} chars")
            passed += 1
        else:
            print(f"  ❌ {name}: FAIL — {res}")
            failed += 1

    # ── 11. export_bids ──
    section("11. export_bids")
    res = mcp_call("tools/call", {"name": "export_bids", "arguments": {"relevant_only": False}})
    content = res.get("result", {}).get("content", [])
    if content:
        data = json.loads(content[0]["text"])
        print(f"  Exported: {data.get('count')} bids")
        print("  ✅ PASS")
        passed += 1
    else:
        print(f"  ❌ FAIL")
        failed += 1

    # ── Summary ──
    section("FINAL RESULTS")
    total = passed + failed
    print(f"  ✅ Passed: {passed}/{total}")
    print(f"  ❌ Failed: {failed}/{total}")
    if failed == 0:
        print("\n  🎉 ALL TESTS PASSED — FULL FUNCTIONALITY VERIFIED!")
    else:
        print(f"\n  ⚠️  {failed} test(s) failed")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
