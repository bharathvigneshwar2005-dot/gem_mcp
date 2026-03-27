"""
GeM MCP Server
==============
A fully-featured, Claude-compatible Model Context Protocol (MCP) server
for interacting with India's Government e-Marketplace (GeM) bid data.

Transport: Streamable HTTP (production-ready, remote-deployable)
Database:  MongoDB (Atlas free tier compatible)
Deploy:    Render (Docker)

Endpoint:  /mcp   (MCP protocol)
           /health (health check for Render)

Usage:
  Local:   python server.py
  Docker:  docker build -t gem-mcp . && docker run -p 8000:8000 gem-mcp
  Render:  Push to GitHub → connect to Render → deploy

Clients:
  Claude Code:  claude mcp add --transport http gem-server https://your-app.onrender.com/mcp
  MCP Inspector: npx -y @modelcontextprotocol/inspector → connect to http://localhost:8000/mcp
"""

import os
import json
import logging
import contextlib
from datetime import datetime

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import gem_scraper

load_dotenv()

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gem.mcp")

# ─── Shared State ──────────────────────────────────────────────────────────────
# These are initialized in the lifespan and shared across requests.

_state = {
    "mongo_client": None,
    "db": None,
    "http_session": None,
}


# ─── FastMCP Server ───────────────────────────────────────────────────────────

mcp = FastMCP(
    name="GeM Marketplace",
    instructions="""You are connected to the GeM (Government e-Marketplace) MCP server.
This server provides tools to search, crawl, and analyze public procurement bid listings
from India's GeM portal (bidplus.gem.gov.in).

Available capabilities:
- Search bids by keyword, ministry, organization, date range
- Crawl latest bid listings from the portal
- Find drone/UAV-specific procurement opportunities
- Query stored bid data with flexible filters
- Get bid detail pages for specific bids
- View database statistics and keyword configurations

Data is scraped from the public GeM portal. No authentication required.
Bids are stored in MongoDB for fast querying and persistence.

Tips:
- Use 'search_bids' for keyword-based searches on the live GeM portal
- Use 'get_stored_bids' to query previously scraped data from the database
- Use 'search_drone_bids' to find all drone/UAV-related procurement
- Use 'get_bid_detail' to fetch extended info about a specific bid
""",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ─── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def search_bids(
    keyword: str,
    search_type: str = "fullText",
    max_results: int = 50,
    save_to_db: bool = True,
) -> dict:
    """Search for bids on the live GeM portal by keyword.

    This hits the GeM AJAX API in real-time to find matching bids.

    Args:
        keyword: Search term (e.g. "laptop", "drone", "furniture", "army")
        search_type: Search mode - "fullText" (default), "contains", or "exact"
        max_results: Maximum number of results (1-100, default: 50)
        save_to_db: Whether to save results to the database (default: true)

    Returns:
        Dict with search results including bid listings and count.
    """
    max_results = max(1, min(max_results, 100))
    session = _state["http_session"] or gem_scraper.make_session()
    bids = gem_scraper.search_bids(
        session, keyword,
        search_type=search_type,
        max_results=max_results,
    )

    db_result = None
    if save_to_db and bids and _state["db"] is not None:
        db_result = gem_scraper.upsert_bids(_state["db"], bids)

    return {
        "query": {"keyword": keyword, "search_type": search_type},
        "count": len(bids),
        "bids": bids,
        "db_update": db_result,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
def get_latest_bids(
    max_pages: int = 5,
    save_to_db: bool = True,
) -> dict:
    """Crawl the latest bid listings from the GeM portal.

    Fetches recent bids from the all-bids page. Each page contains ~10 bids.
    Use a small max_pages value (1-5) for quick results, larger (10-50) for broader coverage.

    Args:
        max_pages: Number of pages to crawl (1-50, default: 5). Each page ≈ 10 bids.
        save_to_db: Whether to save results to the database (default: true)

    Returns:
        Dict with crawled bids, count, and optional DB update stats.
    """
    max_pages = max(1, min(max_pages, 50))  # Clamp to 1-50
    session = _state["http_session"] or gem_scraper.make_session()
    bids = gem_scraper.crawl_all_bids(session, max_pages=max_pages)

    db_result = None
    if save_to_db and bids and _state["db"] is not None:
        db_result = gem_scraper.upsert_bids(_state["db"], bids)

    return {
        "pages_crawled": max_pages,
        "count": len(bids),
        "bids": bids,
        "db_update": db_result,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
def search_drone_bids(
    save_to_db: bool = True,
) -> dict:
    """Search for all drone/UAV-related bids on the GeM portal.

    Performs targeted searches across multiple drone-related keywords including:
    drone, UAV, unmanned aerial, quadcopter, hexacopter, surveillance drone,
    ISR drone, loitering munition, swarm, VTOL, RPAS, and more.

    This is useful for defense/aerospace companies looking for procurement opportunities.

    Args:
        save_to_db: Whether to save results to the database (default: true)

    Returns:
        Dict with all drone/UAV-relevant bids found, count, and keywords used.
    """
    session = _state["http_session"] or gem_scraper.make_session()
    bids = gem_scraper.crawl_vortex_bids(session)

    db_result = None
    if save_to_db and bids and _state["db"] is not None:
        db_result = gem_scraper.upsert_bids(_state["db"], bids)

    return {
        "keywords_searched": gem_scraper.VORTEX_KEYWORDS,
        "count": len(bids),
        "bids": bids,
        "db_update": db_result,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
def get_stored_bids(
    status: str = "",
    relevant_only: bool = False,
    ministry: str = "",
    buyer_org: str = "",
    keyword: str = "",
    bid_type: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_by: str = "end_date",
    sort_order: str = "desc",
) -> dict:
    """Query previously scraped bids stored in the database.

    Use this to search through already-collected bid data without hitting the GeM portal.
    Supports filtering by status, relevance, ministry, buyer org, keyword, and bid type.

    Args:
        status: Filter by bid status (e.g. "open", "closed", "awarded")
        relevant_only: If true, return only drone/UAV-relevant bids
        ministry: Filter by ministry name (partial match, case-insensitive)
        buyer_org: Filter by buyer organization (partial match, case-insensitive)
        keyword: Search across title, ministry, and buyer_org fields
        bid_type: Filter by bid type ("BID", "RA", "Service BID")
        limit: Maximum number of results to return (1-200, default: 50)
        skip: Number of results to skip for pagination (default: 0)
        sort_by: Field to sort by (default: "end_date")
        sort_order: Sort direction - "asc" or "desc" (default: "desc")

    Returns:
        Dict with matching bids, count, and applied filters.
    """
    if _state["db"] is None:
        return {"error": "Database not connected", "bids": [], "count": 0}

    limit = max(1, min(limit, 200))
    bids = gem_scraper.query_bids(
        _state["db"],
        status=status or None,
        relevant_only=relevant_only,
        ministry=ministry or None,
        buyer_org=buyer_org or None,
        keyword=keyword or None,
        bid_type=bid_type or None,
        limit=limit,
        skip=skip,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    return {
        "filters": {
            "status": status,
            "relevant_only": relevant_only,
            "ministry": ministry,
            "buyer_org": buyer_org,
            "keyword": keyword,
            "bid_type": bid_type,
        },
        "pagination": {"limit": limit, "skip": skip, "sort_by": sort_by, "sort_order": sort_order},
        "count": len(bids),
        "bids": bids,
    }


@mcp.tool()
def get_bid_detail(
    bid_no: str = "",
    detail_url: str = "",
) -> dict:
    """Fetch the full detail page for a specific bid from the GeM portal.

    Scrapes the bid detail page to extract extended information like specifications,
    estimated value, delivery location, item categories, and any tabular data.

    Provide either a bid number or a detail URL.

    Args:
        bid_no: The bid number (e.g. "GEM/2024/B/1234567")
        detail_url: Direct URL to the bid detail page (optional, overrides bid_no)

    Returns:
        Dict with extended bid details including specifications and metadata.
    """
    if not bid_no and not detail_url:
        return {"error": "Provide either bid_no or detail_url"}

    session = _state["http_session"] or gem_scraper.make_session()

    # If bid_no given, try to find stored URL first
    if bid_no and not detail_url and _state["db"] is not None:
        stored = gem_scraper.get_bid_by_number(_state["db"], bid_no)
        if stored and stored.get("detail_url"):
            detail_url = stored["detail_url"]

    detail = gem_scraper.fetch_bid_detail(session, bid_no=bid_no, detail_url=detail_url)

    if detail is None:
        return {"error": f"Could not fetch detail for bid {bid_no or detail_url}"}

    return {
        "bid_no": bid_no,
        "detail": detail,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
def get_bid_stats() -> dict:
    """Get summary statistics about the bid database.

    Returns total bid count, drone/UAV-relevant count, open bids count,
    last scrape time, top ministries by bid count, and bid type breakdown.

    Returns:
        Dict with comprehensive database statistics.
    """
    if _state["db"] is None:
        return {"error": "Database not connected"}

    stats = gem_scraper.get_stats(_state["db"])
    return stats


@mcp.tool()
def lookup_bid(bid_no: str) -> dict:
    """Look up a specific bid by its bid number from the database.

    Args:
        bid_no: The bid number to look up (e.g. "GEM/2024/B/1234567")

    Returns:
        Dict with the bid data if found, or an error message.
    """
    if _state["db"] is None:
        return {"error": "Database not connected"}

    bid = gem_scraper.get_bid_by_number(_state["db"], bid_no)
    if bid is None:
        return {
            "error": f"Bid {bid_no} not found in database",
            "suggestion": "Try using search_bids to find it on the live portal first",
        }

    return {"bid": bid}


@mcp.tool()
def cleanup_old_bids(days: int = 90) -> dict:
    """Delete bids older than a specified number of days from the database.

    Useful for database maintenance and keeping storage within free tier limits.

    Args:
        days: Delete bids scraped more than this many days ago (default: 90)

    Returns:
        Dict with the number of bids deleted.
    """
    if _state["db"] is None:
        return {"error": "Database not connected"}

    days = max(1, min(days, 365))
    deleted = gem_scraper.delete_old_bids(_state["db"], days=days)
    return {
        "deleted_count": deleted,
        "older_than_days": days,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
def export_bids(relevant_only: bool = False) -> dict:
    """Export all stored bids as JSON data.

    Args:
        relevant_only: If true, export only drone/UAV-relevant bids

    Returns:
        Dict with the exported bids array and count.
    """
    if _state["db"] is None:
        return {"error": "Database not connected"}

    bids = gem_scraper.export_json(_state["db"], relevant_only=relevant_only)
    return {
        "count": len(bids),
        "relevant_only": relevant_only,
        "bids": bids,
    }


# ─── MCP Resources ────────────────────────────────────────────────────────────

@mcp.resource("gem://keywords")
def get_keywords() -> str:
    """Current list of monitored drone/UAV keywords used for relevance tagging."""
    return json.dumps({
        "keywords": gem_scraper.VORTEX_KEYWORDS,
        "count": len(gem_scraper.VORTEX_KEYWORDS),
        "description": "Keywords used to automatically tag bids as drone/UAV-relevant",
    }, indent=2)


@mcp.resource("gem://config")
def get_config() -> str:
    """Current server configuration (non-sensitive)."""
    return json.dumps({
        "base_url": gem_scraper.BASE_URL,
        "all_bids_data_url": gem_scraper.ALL_BIDS_DATA,
        "bid_detail_url": gem_scraper.BID_DETAIL_URL,
        "request_delay_sec": gem_scraper.REQUEST_DELAY_SEC,
        "mongodb_db": os.getenv("MONGODB_DB", "gem_mcp"),
        "mongodb_connected": _state["db"] is not None,
    }, indent=2)


@mcp.resource("gem://stats")
def get_stats_resource() -> str:
    """Current database statistics including bid counts, top ministries, and last scrape time."""
    if _state["db"] is None:
        return json.dumps({"error": "Database not connected"})
    stats = gem_scraper.get_stats(_state["db"])
    return json.dumps(stats, indent=2, default=str)


@mcp.resource("gem://urls")
def get_gem_urls() -> str:
    """GeM portal URL structure and endpoints used by this scraper."""
    return json.dumps({
        "base": gem_scraper.BASE_URL,
        "endpoints": {
            "all_bids_page": gem_scraper.ALL_BIDS_PAGE,
            "all_bids_data_api": gem_scraper.ALL_BIDS_DATA,
            "bid_detail": gem_scraper.BID_DETAIL_URL,
            "bid_other_details": gem_scraper.BID_OTHER_DETAILS,
        },
        "api_format": {
            "method": "POST",
            "payload": "JSON-stringified {page, param: {searchBid, searchType}, filter: {sort, bidStatusType, byType}}",
            "csrf": "Token from csrf_gem_cookie sent as csrf_bd_gem_nk",
            "response": "Solr JSON: {code, response: {response: {numFound, start, docs}}}",
        },
    }, indent=2)


# ─── MCP Prompts ───────────────────────────────────────────────────────────────

@mcp.prompt()
def analyze_procurement(sector: str = "defence") -> str:
    """Generate a prompt for analyzing procurement opportunities in a sector."""
    return f"""Analyze the current Government e-Marketplace (GeM) procurement landscape for the {sector} sector.

Steps:
1. First, search for relevant bids using the search_bids tool with keywords related to "{sector}"
2. Check the database statistics using get_bid_stats
3. Look at stored bids using get_stored_bids filtered by relevant criteria
4. Provide a summary including:
   - Total number of relevant bids found
   - Key buying organizations and ministries
   - Upcoming deadlines (open bids with nearest end dates)
   - Estimated values where available
   - Recommendations for which bids to pursue
"""


@mcp.prompt()
def drone_opportunity_report() -> str:
    """Generate a comprehensive drone/UAV procurement opportunity report."""
    return """Generate a comprehensive report on drone/UAV procurement opportunities on GeM.

Steps:
1. Run search_drone_bids to fetch the latest drone-related bids from the portal
2. Use get_stored_bids with relevant_only=true to see all stored drone bids
3. Get statistics with get_bid_stats
4. For the most promising bids, use get_bid_detail to get full specifications
5. Compile a report including:
   - Executive summary of the drone procurement landscape
   - List of open opportunities sorted by deadline
   - Breakdown by buying ministry/organization
   - Estimated values and quantities
   - Key specifications and requirements
   - Recommendations on which opportunities to prioritize
"""


@mcp.prompt()
def bid_deep_dive(bid_no: str) -> str:
    """Generate a detailed analysis prompt for a specific bid."""
    return f"""Perform a deep-dive analysis of bid {bid_no} from the GeM portal.

Steps:
1. Look up the bid using lookup_bid with bid_no="{bid_no}"
2. If found, get the full detail page using get_bid_detail with bid_no="{bid_no}"
3. Search for similar bids using search_bids with keywords from this bid's title
4. Provide a comprehensive analysis including:
   - Full bid details and specifications
   - Buying organization and ministry context
   - Quantity, timeline, and estimated value
   - Similar past/current bids for comparison
   - Risk assessment and recommendations
   - Key deadlines and action items
"""


# ─── Health Check Endpoint ─────────────────────────────────────────────────────

async def health_check(request):
    """Health check endpoint for Render and monitoring."""
    db_status = "connected" if _state["db"] is not None else "disconnected"
    try:
        if _state["mongo_client"]:
            _state["mongo_client"].admin.command("ping")
            db_status = "connected"
    except Exception:
        db_status = "error"

    status_code = 200 if db_status == "connected" else 503

    return JSONResponse(
        {
            "status": "healthy" if db_status == "connected" else "degraded",
            "service": "gem-mcp-server",
            "database": db_status,
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0",
        },
        status_code=status_code,
    )


# ─── ASGI App (Starlette + FastMCP) ───────────────────────────────────────────

@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """Application lifespan: initialize MongoDB and HTTP session on startup."""
    log.info("Starting GeM MCP Server...")

    # Initialize MongoDB
    try:
        _state["mongo_client"] = gem_scraper.get_mongo_client()
        _state["db"] = gem_scraper.get_db(_state["mongo_client"])
        log.info("MongoDB connected and indexes ensured")
    except Exception as e:
        log.error(f"MongoDB init failed: {e}")
        log.warning("Server will run without database — live scraping still works")

    # Initialize shared HTTP session for scraping
    _state["http_session"] = gem_scraper.make_session()
    log.info("HTTP session initialized")

    # Run MCP session manager
    async with mcp.session_manager.run():
        log.info("MCP session manager started")
        yield

    # Cleanup
    log.info("Shutting down GeM MCP Server...")
    if _state["mongo_client"]:
        _state["mongo_client"].close()
        log.info("MongoDB connection closed")


# Configure MCP path to exactly /mcp (without trailing slash)
mcp.settings.streamable_http_path = "/mcp"
fastmcp_app = mcp.streamable_http_app()

# Build the Starlette app by combining our health check with FastMCP's internal routes
# This avoids using Mount("/mcp", ...), which causes 307 Temporary Redirects for POST requests
app = Starlette(
    routes=[
        Route("/health", health_check),
    ] + fastmcp_app.routes,
    lifespan=lifespan,
)

# Add CORS middleware for browser-based clients (Claude Web, etc.)
app = CORSMiddleware(
    app,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")

    log.info(f"Starting server on {host}:{port}")
    log.info(f"MCP endpoint: http://{host}:{port}/mcp")
    log.info(f"Health check: http://{host}:{port}/health")

    uvicorn.run(app, host=host, port=port)
