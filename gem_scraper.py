"""
GeM Bid Scraper — AJAX API Version
====================================
Scrapes public bid listings from https://bidplus.gem.gov.in
using the reverse-engineered AJAX API endpoint.

No login required for public bid data.

API Details (reverse-engineered):
  Endpoint:   POST https://bidplus.gem.gov.in/all-bids-data
  Auth:       CSRF token from cookie 'csrf_gem_cookie' → field 'csrf_bd_gem_nk'
  Payload:    JSON-stringified object with:
    - page:   int (1-indexed page number)
    - param:  { searchBid: str, searchType: "fullText"|"contains"|"exact" }
    - filter: { sort: str, bidStatusType: str, byType: str }
  Response:   Solr-style JSON:
    { code: 200, response: { response: { numFound, start, docs: [...] } } }

  Bid detail: GET https://bidplus.gem.gov.in/showbidDocument/{bid_id}
  Other info: POST https://bidplus.gem.gov.in/public-bid-other-details/
"""

import requests
import json
import time
import re
import logging
import os
from datetime import datetime
from pymongo import MongoClient, DESCENDING
from pymongo.errors import ConnectionFailure
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ─── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://bidplus.gem.gov.in"
ALL_BIDS_PAGE = f"{BASE_URL}/all-bids"
ALL_BIDS_DATA = f"{BASE_URL}/all-bids-data"
BID_DETAIL_URL = f"{BASE_URL}/showbidDocument"
BID_OTHER_DETAILS = f"{BASE_URL}/public-bid-other-details"

REQUEST_DELAY_SEC = float(os.getenv("REQUEST_DELAY_SEC", "1.5"))

# Keywords for drone/UAV related bids
VORTEX_KEYWORDS = [
    "drone", "uav", "unmanned aerial", "unmanned aircraft",
    "quadcopter", "hexacopter", "octocopter",
    "surveillance drone", "isr drone", "fpv",
    "loitering munition", "swarm",
    "aerial surveillance", "uas", "rpas",
    "vtol", "fixed wing drone",
]

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gem")

# ─── MongoDB ───────────────────────────────────────────────────────────────────

def get_mongo_client() -> MongoClient:
    """Create and return a MongoDB client from env config."""
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        log.info("Connected to MongoDB")
    except ConnectionFailure as e:
        log.error(f"MongoDB connection failed: {e}")
        raise
    return client


def get_db(client: MongoClient = None):
    """Get the GeM database. Creates indexes on first call."""
    if client is None:
        client = get_mongo_client()
    db_name = os.getenv("MONGODB_DB", "gem_mcp")
    db = client[db_name]
    bids = db.bids
    bids.create_index("bid_no", unique=True)
    bids.create_index("end_date", sparse=True)
    bids.create_index("status")
    bids.create_index("is_relevant")
    bids.create_index("ministry")
    bids.create_index("buyer_org")
    bids.create_index([("title", "text"), ("ministry", "text"), ("buyer_org", "text")])
    return db


# ─── HTTP Session + CSRF ──────────────────────────────────────────────────────

def make_session() -> requests.Session:
    """Returns a session configured with browser headers."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": BASE_URL,
    })
    return s


def refresh_csrf(session: requests.Session) -> str:
    """Visit the all-bids page to get a fresh CSRF token from cookies."""
    log.debug("Refreshing CSRF token...")
    try:
        r = session.get(ALL_BIDS_PAGE, timeout=30)
        r.raise_for_status()
        token = session.cookies.get("csrf_gem_cookie", "")
        if token:
            log.debug(f"CSRF token obtained: {token[:10]}...")
        else:
            log.warning("No CSRF token in cookies")
        return token
    except requests.RequestException as e:
        log.error(f"Failed to refresh CSRF: {e}")
        return ""


# ─── Core API Call ─────────────────────────────────────────────────────────────

def fetch_bids_api(session: requests.Session, csrf_token: str,
                   page: int = None, search_keyword: str = "",
                   search_type: str = "fullText",
                   bid_status: str = "all_bids",
                   sort: str = "Bid-End-Date-Latest",
                   retries: int = 2) -> dict | None:
    """
    Call the GeM AJAX API to fetch bid data.

    Args:
        session: requests Session with cookies
        csrf_token: CSRF token from csrf_gem_cookie
        page: Page number (1-indexed, None for first page)
        search_keyword: Search text
        search_type: "fullText", "contains", or "exact"
        bid_status: "all_bids", "ongoing_bids", "bidrastatus"
        sort: Sort field name
        retries: Number of retry attempts

    Returns:
        Dict with {numFound, start, docs} or None on failure
    """
    postdata = {}
    if page is not None:
        postdata["page"] = page

    postdata["param"] = {
        "searchBid": search_keyword,
        "searchType": search_type,
    }
    postdata["filter"] = {
        "sort": sort,
        "bidStatusType": bid_status,
        "byType": "all_type",
    }

    payload = {
        "payload": json.dumps(postdata),
        "csrf_bd_gem_nk": csrf_token,
    }

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": ALL_BIDS_PAGE,
    }

    for attempt in range(1, retries + 1):
        try:
            r = session.post(ALL_BIDS_DATA, data=payload, headers=headers, timeout=30)

            if r.status_code == 403:
                log.warning("Got 403, refreshing CSRF token...")
                csrf_token = refresh_csrf(session)
                payload["csrf_bd_gem_nk"] = csrf_token
                continue

            r.raise_for_status()
            data = r.json()

            if data.get("code") == 200:
                response = data.get("response", {}).get("response", {})
                return {
                    "numFound": response.get("numFound", 0),
                    "start": response.get("start", 0),
                    "docs": response.get("docs", []),
                }
            else:
                log.warning(f"API returned code {data.get('code')}: {data.get('message', '')}")
                return None

        except requests.RequestException as e:
            log.warning(f"Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(REQUEST_DELAY_SEC * attempt)
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON response: {e}")
            return None

    return None


def parse_solr_doc(doc: dict) -> dict:
    """
    Convert a Solr document from the GeM API into a clean bid dict.
    Solr wraps most values in arrays, so we unwrap them.
    """
    def unwrap(val):
        """Unwrap Solr single-element arrays."""
        if isinstance(val, list) and len(val) == 1:
            return val[0]
        elif isinstance(val, list) and len(val) == 0:
            return ""
        return val

    bid_no = str(unwrap(doc.get("b_bid_number", "")))

    # Parse bid type
    bid_type_code = unwrap(doc.get("b_bid_type", 0))
    bid_type_map = {0: "BID", 1: "BID", 2: "RA", 3: "Service BID"}
    bid_type = bid_type_map.get(bid_type_code, "BID")

    # Parse status
    status_code = unwrap(doc.get("b_status", 0))
    b_type = unwrap(doc.get("b_type", 0))
    if status_code == 0:
        status = "open"
    elif status_code == 1:
        status = "closed"
    else:
        status = "awarded"

    # Parse dates from Solr datetime format (2025-09-05T17:00:00Z)
    end_date_raw = str(unwrap(doc.get("final_end_date_sort", "")))
    start_date_raw = str(unwrap(doc.get("b_start_date_sort", end_date_raw)))

    def parse_solr_date(dt_str):
        if not dt_str or dt_str == "":
            return ""
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.strftime("%d-%m-%Y")
        except (ValueError, AttributeError):
            return dt_str

    # Build the bid_id for detail URL
    bid_id = str(unwrap(doc.get("b_id", doc.get("id", ""))))

    return {
        "bid_no": bid_no,
        "title": str(unwrap(doc.get("b_category_name", doc.get("bd_category_name", "")))),
        "quantity": str(unwrap(doc.get("b_total_quantity", ""))),
        "uom": "",
        "start_date": parse_solr_date(start_date_raw),
        "end_date": parse_solr_date(end_date_raw),
        "ministry": str(unwrap(doc.get("ba_official_details_minName", ""))),
        "buyer_org": str(unwrap(doc.get("ba_official_details_deptName", ""))),
        "bid_type": bid_type,
        "status": status,
        "detail_url": f"{BID_DETAIL_URL}/{bid_id}" if bid_id else "",
        "bid_id": bid_id,
        "is_bunch": bool(unwrap(doc.get("b_is_bunch", 0))),
        "is_high_value": bool(unwrap(doc.get("is_high_value", False))),
        "scraped_at": datetime.now().isoformat(),
    }


# ─── Search ────────────────────────────────────────────────────────────────────

def search_bids(session: requests.Session, keyword: str,
                search_type: str = "fullText",
                max_results: int = 50) -> list[dict]:
    """
    Search for bids by keyword using the GeM AJAX API.

    Args:
        session: HTTP session
        keyword: Search keyword
        search_type: "fullText", "contains", or "exact"
        max_results: Maximum number of results to return

    Returns:
        List of bid dicts matching the keyword
    """
    log.info(f"Searching GeM for: '{keyword}' (type={search_type})")
    csrf = refresh_csrf(session)
    if not csrf:
        log.error("Cannot search without CSRF token")
        return []

    all_bids = []
    page = 1
    pages_needed = (max_results + 9) // 10  # 10 per page

    while len(all_bids) < max_results and page <= pages_needed:
        result = fetch_bids_api(
            session, csrf,
            page=page if page > 1 else None,
            search_keyword=keyword,
            search_type=search_type,
        )

        if result is None:
            break

        docs = result.get("docs", [])
        if not docs:
            break

        for doc in docs:
            bid = parse_solr_doc(doc)
            if bid["bid_no"]:
                all_bids.append(bid)

        total = result.get("numFound", 0)
        log.info(f"  Page {page}: {len(docs)} bids (total available: {total})")

        if len(all_bids) >= total or len(all_bids) >= max_results:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    log.info(f"  → {len(all_bids)} bids collected")
    return all_bids[:max_results]


# ─── Full Crawl ────────────────────────────────────────────────────────────────

def crawl_all_bids(session: requests.Session, max_pages: int = 5) -> list[dict]:
    """
    Crawl the all-bids listing. Each page = 10 bids.
    """
    log.info(f"Crawling all-bids ({max_pages} pages)...")
    csrf = refresh_csrf(session)
    if not csrf:
        return []

    all_bids = []
    for page in range(1, max_pages + 1):
        result = fetch_bids_api(
            session, csrf,
            page=page if page > 1 else None,
        )

        if result is None:
            log.error(f"Failed to fetch page {page}, stopping")
            break

        docs = result.get("docs", [])
        if not docs:
            log.info(f"No docs on page {page}, reached end")
            break

        for doc in docs:
            bid = parse_solr_doc(doc)
            if bid["bid_no"]:
                all_bids.append(bid)

        total = result.get("numFound", 0)
        log.info(f"  Page {page}/{max_pages}: {len(docs)} bids (total: {total})")

        if page >= max_pages:
            break
        time.sleep(REQUEST_DELAY_SEC)

    # Deduplicate
    seen = set()
    unique = []
    for b in all_bids:
        if b["bid_no"] not in seen:
            seen.add(b["bid_no"])
            unique.append(b)

    log.info(f"Crawl complete: {len(unique)} unique bids")
    return unique


def crawl_vortex_bids(session: requests.Session, max_per_keyword: int = 20) -> list[dict]:
    """
    Search for all drone/UAV-relevant keywords.
    """
    all_bids = []
    seen = set()

    for keyword in VORTEX_KEYWORDS:
        bids = search_bids(session, keyword, max_results=max_per_keyword)
        for b in bids:
            if b["bid_no"] not in seen:
                seen.add(b["bid_no"])
                all_bids.append(b)
        time.sleep(REQUEST_DELAY_SEC)

    log.info(f"Vortex crawl: {len(all_bids)} relevant bids found")
    return all_bids


# ─── Bid Detail Page ──────────────────────────────────────────────────────────

def fetch_bid_detail(session: requests.Session, bid_no: str = None,
                     detail_url: str = None, bid_id: str = None) -> dict | None:
    """
    Fetch an individual bid detail page for extended information.
    """
    if not detail_url:
        if bid_id:
            detail_url = f"{BID_DETAIL_URL}/{bid_id}"
        elif bid_no:
            parts = bid_no.split("/")
            if len(parts) >= 4:
                detail_url = f"{BID_DETAIL_URL}/{parts[-1]}"

    if not detail_url:
        return None

    log.info(f"Fetching bid detail: {detail_url}")
    try:
        r = session.get(detail_url, timeout=30)
        if r.status_code != 200:
            log.warning(f"Detail page returned {r.status_code}")
            return None

        soup = BeautifulSoup(r.text, "lxml")
        detail = {
            "bid_no": bid_no or "",
            "detail_url": detail_url,
            "fetched_at": datetime.now().isoformat(),
        }

        full_text = soup.get_text(" ", strip=True)

        # Extract from tables
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True).lower().replace(" ", "_")
                    val = cells[1].get_text(strip=True)
                    if key and val and len(key) < 50:
                        detail[key] = val

        # Extract estimated value
        val_match = re.search(
            r"(?:Estimated|Total)\s*(?:Value|Amount)[:\s]*₹?\s*([\d,]+(?:\.\d+)?)",
            full_text, re.I
        )
        if val_match:
            detail["estimated_value"] = val_match.group(1).replace(",", "")

        # Category
        cat_match = re.search(r"(?:Category|Product\s*Category)[:\s]+([^\n|•]+)", full_text, re.I)
        if cat_match:
            detail["category"] = cat_match.group(1).strip()[:200]

        return detail

    except requests.RequestException as e:
        log.error(f"Detail fetch failed: {e}")
        return None


# ─── Database Operations (MongoDB) ─────────────────────────────────────────────

def upsert_bids(db, bids: list[dict]) -> dict:
    """Insert or update bids in MongoDB. Returns {inserted, updated, total}."""
    now = datetime.now().isoformat()
    inserted = 0
    updated = 0
    collection = db.bids

    for bid in bids:
        is_relevant = any(
            kw.lower() in (bid.get("title", "") + " " + bid.get("buyer_org", "") + " " + bid.get("ministry", "")).lower()
            for kw in VORTEX_KEYWORDS
        )

        bid_doc = {
            "bid_no": bid["bid_no"],
            "title": bid.get("title", ""),
            "quantity": bid.get("quantity", ""),
            "uom": bid.get("uom", ""),
            "start_date": bid.get("start_date", ""),
            "end_date": bid.get("end_date", ""),
            "ministry": bid.get("ministry", ""),
            "buyer_org": bid.get("buyer_org", ""),
            "bid_type": bid.get("bid_type", ""),
            "status": bid.get("status", "open"),
            "detail_url": bid.get("detail_url", ""),
            "bid_id": bid.get("bid_id", ""),
            "is_relevant": is_relevant,
            "is_bunch": bid.get("is_bunch", False),
            "is_high_value": bid.get("is_high_value", False),
            "updated_at": now,
        }

        result = collection.update_one(
            {"bid_no": bid["bid_no"]},
            {
                "$set": bid_doc,
                "$setOnInsert": {"scraped_at": bid.get("scraped_at", now), "notes": ""},
            },
            upsert=True,
        )

        if result.upserted_id:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1

    return {"inserted": inserted, "updated": updated, "total": len(bids)}


def query_bids(db, status: str = None, relevant_only: bool = False,
               ministry: str = None, buyer_org: str = None,
               keyword: str = None, bid_type: str = None,
               limit: int = 50, skip: int = 0,
               sort_by: str = "end_date", sort_order: str = "desc") -> list[dict]:
    """Query stored bids from MongoDB with flexible filters."""
    query = {}
    if status:
        query["status"] = {"$regex": status, "$options": "i"}
    if relevant_only:
        query["is_relevant"] = True
    if ministry:
        query["ministry"] = {"$regex": ministry, "$options": "i"}
    if buyer_org:
        query["buyer_org"] = {"$regex": buyer_org, "$options": "i"}
    if bid_type:
        query["bid_type"] = {"$regex": bid_type, "$options": "i"}
    if keyword:
        query["$or"] = [
            {"title": {"$regex": keyword, "$options": "i"}},
            {"ministry": {"$regex": keyword, "$options": "i"}},
            {"buyer_org": {"$regex": keyword, "$options": "i"}},
        ]

    sort_dir = DESCENDING if sort_order == "desc" else 1
    cursor = db.bids.find(query, {"_id": 0}).sort(sort_by, sort_dir).skip(skip).limit(limit)
    return list(cursor)


def get_stats(db) -> dict:
    """Get summary statistics from the database."""
    collection = db.bids
    total = collection.count_documents({})
    relevant = collection.count_documents({"is_relevant": True})
    open_bids = collection.count_documents({"status": "open"})

    latest = collection.find_one({}, {"scraped_at": 1, "_id": 0}, sort=[("scraped_at", DESCENDING)])
    latest_scrape = latest.get("scraped_at", "never") if latest else "never"

    pipeline = [
        {"$match": {"ministry": {"$ne": ""}}},
        {"$group": {"_id": "$ministry", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    ministry_breakdown = [
        {"ministry": doc["_id"], "count": doc["count"]}
        for doc in collection.aggregate(pipeline)
    ]

    type_pipeline = [
        {"$group": {"_id": "$bid_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    type_breakdown = [
        {"bid_type": doc["_id"], "count": doc["count"]}
        for doc in collection.aggregate(type_pipeline)
    ]

    return {
        "total_bids": total,
        "drone_uav_relevant": relevant,
        "open_bids": open_bids,
        "latest_scrape": latest_scrape,
        "top_ministries": ministry_breakdown,
        "bid_type_breakdown": type_breakdown,
    }


def get_bid_by_number(db, bid_no: str) -> dict | None:
    """Look up a single bid by its bid number."""
    return db.bids.find_one({"bid_no": bid_no}, {"_id": 0})


def delete_old_bids(db, days: int = 90) -> int:
    """Delete bids older than N days."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    result = db.bids.delete_many({"scraped_at": {"$lt": cutoff}})
    return result.deleted_count


def export_json(db, relevant_only: bool = False) -> list[dict]:
    """Export bids as JSON-serializable list."""
    query = {"is_relevant": True} if relevant_only else {}
    cursor = db.bids.find(query, {"_id": 0}).sort("end_date", DESCENDING)
    return list(cursor)


# ─── Main (CLI) ───────────────────────────────────────────────────────────────

def run(mode: str = "vortex", max_pages: int = 5):
    """CLI entry point."""
    client = get_mongo_client()
    db = get_db(client)
    session = make_session()

    if mode == "vortex":
        bids = crawl_vortex_bids(session)
    elif mode == "full":
        bids = crawl_all_bids(session, max_pages=50)
    elif mode == "search":
        keyword = input("Search keyword: ") if len(__import__("sys").argv) < 3 else __import__("sys").argv[2]
        bids = search_bids(session, keyword)
    else:  # latest
        bids = crawl_all_bids(session, max_pages=max_pages)

    if bids:
        result = upsert_bids(db, bids)
        log.info(f"Database updated: {result}")

    stats = get_stats(db)
    print(json.dumps(stats, indent=2))
    client.close()
    return stats


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "latest"
    run(mode=mode)
