#!/usr/bin/env python3
"""
gem_runner.py  —  Daily runner / scheduler for the GeM Bid Scraper
===================================================================

Run manually:
  python gem_runner.py                 # Vortex-relevant bids only
  python gem_runner.py latest          # Last 50 pages of all bids
  python gem_runner.py full            # Full crawl (slow)

Schedule daily (cron):
  0 8 * * * /usr/bin/python3 /path/to/gem_runner.py >> /var/log/gem.log 2>&1

For your Vortex application, call run() from this module to integrate.
"""

import sys
import os
import logging

# Add parent dir to path if needed
sys.path.insert(0, os.path.dirname(__file__))

from gem_scraper import run

log = logging.getLogger("gem.runner")

# ── Output config ──────────────────────────────────────────────────────────────
OUTPUT_DIR = "./gem_data"           # Change to your app's data folder

# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "vortex"
    print(f"\n🚁 GeM Bid Scraper — Vortex Autonomous Systems")
    print(f"   Mode: {mode}")
    print(f"   Output: {OUTPUT_DIR}\n")

    results = run(mode=mode, output_dir=OUTPUT_DIR)

    print(f"\n📁 Output files:")
    for k, v in results.items():
        print(f"   {k:15}: {v}")
