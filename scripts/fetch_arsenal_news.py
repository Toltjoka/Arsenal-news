"""
Arsenal Transfer News Bot
--------------------------
Fetches RSS feeds, keeps only Arsenal player transfer news (in/out,
rumor or confirmed), skips injury news, and upserts into Supabase.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

import os
import re
import sys
from datetime import datetime, timezone

import feedparser
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

TABLE = "arsenal_transfer_news"

# RSS feeds worth checking. Add/remove freely.
FEEDS = [
    "https://www.skysports.com/rss/12040", # Sky Sports transfer centre
    "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
    "https://www.arsenal.com/rss.xml",
    "https://www.football.london/all-about/arsenal-fc/?service=rss",
]

# Must mention Arsenal somewhere
ARSENAL_RE = re.compile(r"\barsenal\b", re.I)

# Signals this is transfer-related (in or out)
TRANSFER_RE = re.compile(
    r"\b(sign(s|ing|ed)?|transfer|loan|bid|deal|medical|"
    r"here we go|completes? (a )?move|agree(s|d)? terms|"
    r"linked|target|release clause|swap deal|departure|"
    r"leaves? arsenal|joins? arsenal|exit|sold|sale)\b",
    re.I,
)

# Signals this is an injury story — hard exclude
INJURY_RE = re.compile(
    r"\b(injur(y|ed|ies)|surgery|scan|sidelined|hamstring|"
    r"knee|acl|acl injury|fitness concern|ruled out|"
    r"return date|recovery|setback|torn muscle)\b",
    re.I,
)

# Rough classification of rumor vs confirmed
CONFIRMED_RE = re.compile(
    r"\b(here we go|official|confirms?|completes? (a )?move|"
    r"signs (a |his )?(contract|deal)|unveiled|announced)\b",
    re.I,
)

# Rough direction classification
OUT_RE = re.compile(
    r"\b(leaves? arsenal|departs? arsenal|exit(s|ing)?|"
    r"sold to|joins? .*(from|leaving) arsenal|loan(ed)? out|"
    r"departure)\b",
    re.I,
)


def classify(title, summary):
    text = f"{title} {summary}"
    if not ARSENAL_RE.search(text):
        return None
    if INJURY_RE.search(text) and not TRANSFER_RE.search(text):
        return None
    if not TRANSFER_RE.search(text):
        return None

    direction = "out" if OUT_RE.search(text) else "in"
    status = "confirmed" if CONFIRMED_RE.search(text) else "rumor"
    return direction, status


def guess_player_name(title):
    # crude heuristic: take text before first verb-ish keyword
    cut = re.split(TRANSFER_RE, title, maxsplit=1)[0]
    cut = re.sub(r"[:\-–|].*$", "", cut).strip()
    return cut[:120] if cut else title[:120]


def fetch_entries():
    rows = []
    for feed_url in FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"skip {feed_url}: {e}", file=sys.stderr)
            continue

        source_name = parsed.feed.get("title", feed_url)

        for entry in parsed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            link = entry.get("link")
            if not link:
                continue

            result = classify(title, summary)
            if not result:
                continue
            direction, status = result

            published = entry.get("published_parsed")
            published_at = (
                datetime(*published[:6], tzinfo=timezone.utc).isoformat()
                if published
                else None
            )

            rows.append(
                {
                    "player_name": guess_player_name(title),
                    "direction": direction,
                    "status": status,
                    "headline": title[:300],
                    "source_name": source_name,
                    "source_url": link,
                    "published_at": published_at,
                }
            )
    return rows


def upsert(rows):
    if not rows:
        print("No new matching stories this run.")
        return

    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }
    resp = requests.post(url, headers=headers, json=rows, timeout=30)
    if resp.status_code >= 300:
        print(f"Supabase error {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    print(f"Upserted {len(rows)} candidate rows (duplicates ignored by source_url).")


if __name__ == "__main__":
    upsert(fetch_entries())
