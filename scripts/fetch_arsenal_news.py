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
import traceback
from datetime import datetime, timezone

import feedparser
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

TABLE = "arsenal_transfer_news"

FEEDS = [
    "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
    "https://arseblog.com/feed",
    "https://www.skysports.com/rss/11095",  # Sky Sports football (filtered by keyword)
    "https://www.90min.com/posts.rss",       # broad football feed (filtered by keyword)
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

# Meta/roundup headlines that contain the word "transfer" but aren't
# about a specific move (e.g. "Arsenal's transfer plans"). Excluded
# unless a more specific verb is also present.
GENERIC_RE = re.compile(
    r"\btransfer (plans|window|news|rumours?|targets?|list|state of play|"
    r"round-?up|latest|gossip)\b",
    re.I,
)
SPECIFIC_VERB_RE = re.compile(
    r"\b(sign|bid|loan|joins?|leaves?|complete|medical|agree)\b", re.I
)

# Capitalized word(s) -- candidate names/clubs in a headline.
CAP_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:'s)?(?:\s[A-Z][a-zA-Z]+(?:'s)?){0,2}\b")

# Clubs, competitions, outlets, and stray words that show up capitalized
# in headlines but are never the player. Extend freely -- one line each.
WORD_BLOCKLIST = {
    "arsenal", "bournemouth", "leverkusen", "besiktas",
    "newcastle", "leeds", "chelsea", "liverpool", "city", "united", "real",
    "madrid", "barcelona", "psg", "bayern", "juventus", "inter", "milan",
    "sky", "bbc", "sport", "sports", "premier", "league", "la", "liga",
    "serie", "bundesliga", "ligue", "champions", "europa", "here", "we",
    "go", "mikel", "arteta", "watch", "video", "report", "gossip", "club",
    "world", "cup",
}


def classify(title, summary):
    text = f"{title} {summary}"
    if not ARSENAL_RE.search(text):
        return None
    if GENERIC_RE.search(text) and not SPECIFIC_VERB_RE.search(text):
        return None
    if INJURY_RE.search(text) and not TRANSFER_RE.search(title):
        return None
    if not TRANSFER_RE.search(text):
        return None

    direction = "out" if OUT_RE.search(text) else "in"
    status = "confirmed" if CONFIRMED_RE.search(text) else "rumor"
    return direction, status


def extract_player_name(title):
    """Pick the right-most capitalized name/phrase after the transfer
    verb (headlines put the player near the end: 'X sign [desc] Player'),
    stripping known club/competition/outlet words. Cheap and local --
    no model download, no API call."""
    verb_match = TRANSFER_RE.search(title)
    verb_pos = verb_match.end() if verb_match else len(title) // 2

    candidates = []
    for m in CAP_RE.finditer(title):
        words = []
        for w in m.group().split():
            base = re.sub(r"'s$", "", w.lower())
            if base not in WORD_BLOCKLIST:
                words.append(w)
        if not words:
            continue
        candidates.append((m.start(), " ".join(words)))

    if not candidates:
        return title[:120]  # last resort: keep the row, use full headline

    after = [c for c in candidates if c[0] >= verb_pos]
    if after:
        return after[-1][1][:120]  # right-most candidate after the verb
    candidates.sort(key=lambda c: abs(c[0] - verb_pos))
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
        print(f"{feed_url} -> {len(parsed.entries)} entries (source: {source_name})")

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
        err = f"Supabase error {resp.status_code}: {resp.text}"
        print(err, file=sys.stderr)
        log_debug(err)
        sys.exit(1)
    print(f"Upserted {len(rows)} candidate rows (duplicates ignored by source_url).")


def log_debug(message):
    """Write a line to the bot_debug_log table so failures are visible
    without needing GitHub Actions log access."""
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/bot_debug_log",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={"message": message[:4000]},
            timeout=15,
        )
    except Exception:
        pass  # best-effort logging only


if __name__ == "__main__":
    try:
        rows = fetch_entries()
        print(f"Found {len(rows)} matching stories this run.")
        log_debug(f"OK: found {len(rows)} matching stories this run.")
        upsert(rows)
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        log_debug(f"ERROR:\n{tb}")
        sys.exit(1)
