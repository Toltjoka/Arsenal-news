"""
Arsenal Transfer News Bot
--------------------------
Fetches RSS feeds, keeps only Arsenal player transfer news (in/out,
rumor or confirmed), skips injury news, and upserts into Supabase.

100% free to run: pulls out player names with a capitalized-word +
distance-to-verb heuristic (no paid LLM API, no extra dependencies),
plus keyword rules for direction (in/out) and status (rumor/confirmed).

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

FEEDS = [
    "https://www.skysports.com/rss/12040",  # Sky Sports transfer centre
    "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
    "https://www.arsenal.com/rss.xml",
    "https://www.football.london/all-about/arsenal-fc/?service=rss",
]

ARSENAL_RE = re.compile(r"\barsenal\b", re.I)

TRANSFER_RE = re.compile(
    r"\b(sign(s|ing|ed)?|transfer|loan(ed)?|bid|deal|medical|"
    r"here we go|completes? (a )?move|agree(s|d)? terms|"
    r"linked|target|release clause|swap deal|departure|"
    r"leaves?|joins?|exit(s|ing)?|sold|sale|unveil(ed|s)?)\b",
    re.I,
)

INJURY_RE = re.compile(
    r"\b(injur(y|ed|ies)|surgery|scan|sidelined|hamstring|"
    r"knee|acl|fitness concern|ruled out|return date|"
    r"recovery|setback|torn muscle)\b",
    re.I,
)

CONFIRMED_RE = re.compile(
    r"\b(here we go|official|confirms?|completes? (a )?move|"
    r"signs (a |his )?(contract|deal)|unveiled|announced)\b",
    re.I,
)

# Explicit "leaving Arsenal" signals. Anything else that matches
# TRANSFER_RE defaults to "in" (arriving/being linked to Arsenal) --
# the far more common case in transfer-window headlines.
OUT_RE = re.compile(
    r"\b(leaves? arsenal|departs? arsenal|exit(s|ing)? arsenal|"
    r"sold (by|to)|loan(ed)? out|arsenal (release|sell|sold|loan out)|"
    r"departure)\b",
    re.I,
)

# Capitalized word(s) -- candidate names/clubs in a headline.
CAP_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:'s)?(?:\s[A-Z][a-zA-Z]+(?:'s)?){0,2}\b")

# Clubs, competitions, outlets, and stray words that show up capitalized
# in headlines but are never the player. Extend freely -- one line each.
WORD_BLOCKLIST = {
    "arsenal", "arsenal's", "bournemouth", "leverkusen", "besiktas",
    "newcastle", "leeds", "chelsea", "liverpool", "city", "united", "real",
    "madrid", "barcelona", "psg", "bayern", "juventus", "inter", "milan",
    "sky", "bbc", "sport", "sports", "premier", "league", "la", "liga",
    "serie", "bundesliga", "ligue", "champions", "europa", "here", "we",
    "go", "mikel", "arteta", "watch", "video", "report", "gossip",
}


def classify(title, summary):
    text = f"{title} {summary}"
    if not ARSENAL_RE.search(text):
        return None
    if INJURY_RE.search(text) and not TRANSFER_RE.search(title):
        return None
    if not TRANSFER_RE.search(text):
        return None

    direction = "out" if OUT_RE.search(text) else "in"
    status = "confirmed" if CONFIRMED_RE.search(text) else "rumor"
    return direction, status


def extract_player_name(title):
    """Pick the capitalized name/phrase closest to the transfer verb,
    stripping out known club/competition/outlet words. Cheap and local
    -- no model download, no API call."""
    verb_match = TRANSFER_RE.search(title)
    verb_pos = verb_match.start() if verb_match else len(title) // 2

    candidates = []
    for m in CAP_RE.finditer(title):
        words = [w for w in m.group().split() if w.lower() not in WORD_BLOCKLIST]
        if not words:
            continue
        candidates.append((abs(m.start() - verb_pos), " ".join(words)))

    if not candidates:
        return title[:120]  # last resort: keep the row, use full headline
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1][:120]


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
                    "player_name": extract_player_name(title),
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
    rows = fetch_entries()
    print(f"Found {len(rows)} matching stories this run.")
    upsert(rows)
