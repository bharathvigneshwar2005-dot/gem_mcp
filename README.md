# GeM MCP Server — Government e-Marketplace Intelligence

A fully-featured, Claude-compatible **Model Context Protocol (MCP)** server for interacting with India's Government e-Marketplace (GeM) bid data.

Deploy on **Render** and connect from **Claude Code**, **Claude Web**, or any MCP-compatible agent.

---

## Features

### 🔧 MCP Tools (9 tools)
| Tool | Description |
|------|-------------|
| `search_bids` | Search the live GeM portal by keyword, date, ministry, org |
| `get_latest_bids` | Crawl latest N pages of bid listings |
| `search_drone_bids` | Find all drone/UAV procurement opportunities |
| `get_stored_bids` | Query stored bids with filters (status, ministry, type, etc.) |
| `get_bid_detail` | Fetch extended info from a bid detail page |
| `lookup_bid` | Look up a specific bid by number from the database |
| `get_bid_stats` | Database statistics (counts, top ministries, etc.) |
| `cleanup_old_bids` | Delete bids older than N days |
| `export_bids` | Export all stored bids as JSON |

### 📄 MCP Resources (4 resources)
| URI | Description |
|-----|-------------|
| `gem://keywords` | Monitored drone/UAV keywords list |
| `gem://config` | Server configuration (non-sensitive) |
| `gem://stats` | Live database statistics |
| `gem://urls` | GeM portal URL structure and endpoints |

### 💬 MCP Prompts (3 prompts)
| Prompt | Description |
|--------|-------------|
| `analyze_procurement` | Sector-specific procurement analysis workflow |
| `drone_opportunity_report` | Comprehensive drone/UAV opportunity report |
| `bid_deep_dive` | Detailed analysis of a specific bid |

---

## Quick Start

### 1. Clone and Install
```bash
git clone <your-repo-url>
cd gem_mcp
pip install -r requirements.txt
```

### 2. Configure MongoDB
Copy `.env.example` to `.env` and set your MongoDB connection string:
```bash
cp .env.example .env
```

```env
# MongoDB Atlas free tier
MONGODB_URI=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB=gem_mcp
```

### 3. Run Locally
```bash
python server.py
```

Server starts at `http://localhost:8000`:
- **MCP endpoint**: `http://localhost:8000/mcp`
- **Health check**: `http://localhost:8000/health`

### 4. Connect a Client

**Claude Code:**
```bash
claude mcp add --transport http gem-server http://localhost:8000/mcp
```

**MCP Inspector:**
```bash
npx -y @modelcontextprotocol/inspector
# Connect to: http://localhost:8000/mcp
```

---

## Deploy to Render

### Option A: Blueprint (one-click)
1. Push this repo to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com) → New → Blueprint
3. Connect your repo — Render reads `render.yaml` automatically
4. Set the `MONGODB_URI` environment variable in the Render dashboard

### Option B: Manual Docker Deploy
1. New → Web Service → Docker
2. Point to your repo
3. Set environment variables:
   - `MONGODB_URI` = your Atlas connection string
   - `MONGODB_DB` = `gem_mcp`
4. Health check path: `/health`

### After Deploy
```bash
# Add to Claude Code
claude mcp add --transport http gem-server https://your-app.onrender.com/mcp
```

---

## Architecture

```
Client (Claude Code / Claude Web / Any MCP Agent)
        │
        │ MCP Protocol (Streamable HTTP)
        ▼
┌─────────────────────────────────┐
│  server.py (FastMCP + Starlette)│  ← Render Web Service
│  POST /mcp  — MCP endpoint     │
│  GET  /health — health check   │
├─────────────────────────────────┤
│  gem_scraper.py                 │  ← Scraping + parsing logic
│  (requests + BeautifulSoup)     │
└─────────┬───────────────────────┘
          │                    │
          ▼                    ▼
   MongoDB Atlas        bidplus.gem.gov.in
   (persistent           (live scraping)
    storage)
```

---

## Data Fields

| Field | Description |
|-------|-------------|
| `bid_no` | Unique bid number (e.g. `GEM/2024/B/123456`) |
| `title` | Bid title / product description |
| `quantity` | Number of units required |
| `uom` | Unit of measurement |
| `start_date` | Bid opening date |
| `end_date` | Bid closing deadline |
| `ministry` | Procuring ministry/department |
| `buyer_org` | Buying organization |
| `bid_type` | BID / RA (Reverse Auction) / Service BID |
| `status` | open / closed / awarded |
| `detail_url` | Full URL to bid detail page |
| `is_relevant` | Auto-tagged if matches drone/UAV keywords |

---

## GeM Portal URLs (Reverse-Engineered)

```
BASE:  https://bidplus.gem.gov.in

Listings:
  /all-bids                    → All bids (paginated)
  /bidlists                    → Ongoing bids
  /bidresultlists              → Completed bids
  /advance-search              → Search (POST)

Detail:
  /bidding/bid/getBidResultView/{id}

Search POST params:
  search_bid, ministry, org_name, bid_no, date_from, date_to
```

---

## Rate Limiting

- Default: **1.5s** between requests (configurable via `REQUEST_DELAY_SEC` env var)
- GeM is a public portal — public bid data is freely accessible
- Don't run concurrent crawls to avoid IP blocking

---

## Files

```
gem_mcp/
├── server.py           ← MCP server (main entry point)
├── gem_scraper.py      ← Scraping logic + MongoDB operations
├── requirements.txt    ← Python dependencies
├── Dockerfile          ← Container for Render
├── render.yaml         ← Render blueprint
├── .env.example        ← Environment variable template
├── .gitignore          ← Git ignore rules
└── README.md           ← This file
```
