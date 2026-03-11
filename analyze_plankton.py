#!/usr/bin/env python3
"""
Polymarket planktonXD Trade History Analyzer

Wallet: 0x4ffe49ba2a4cae123536a8af4fda48faeb609f71

Usage:
    pip install requests pandas tabulate
    python analyze_plankton.py
"""

import requests
import json
import time
import re
from datetime import datetime
from collections import defaultdict

WALLET = "0x4ffe49ba2a4cae123536a8af4fda48faeb609f71"

# Polymarket public endpoints
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (analysis script)"})


# ── helpers ────────────────────────────────────────────────────────────────

def get(url, params=None, retries=4):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [retry {attempt+1}] {e} — waiting {wait}s")
            time.sleep(wait)
    return None


def paginate(url, params=None, limit=500, key=None):
    """Collect all pages from a cursor-based or offset-based endpoint."""
    params = dict(params or {})
    params["limit"] = limit
    offset = 0
    results = []
    while True:
        params["offset"] = offset
        data = get(url, params)
        if data is None:
            break
        # handle both list and {"data": [...]} shapes
        page = data if isinstance(data, list) else data.get(key or "data", data)
        if not page:
            break
        results.extend(page)
        print(f"  fetched {len(results)} records…", end="\r")
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.15)   # be polite
    print()
    return results


# ── keyword→category classifier ────────────────────────────────────────────

CATEGORY_PATTERNS = {
    "Crypto":       r"\b(btc|bitcoin|eth|ethereum|sol|solana|xrp|bnb|crypto|token|defi|nft|coin|usdc|usdt|doge|pepe|shib)\b",
    "Sports":       r"\b(nba|nfl|mlb|nhl|ufc|mma|soccer|football|basketball|tennis|golf|f1|formula|champion|league|cup|match|game|player|team|win|beat|score)\b",
    "Politics":     r"\b(elect|president|senate|congress|democrat|republican|party|vote|poll|governor|minister|prime|trump|biden|harris|macron|putin)\b",
    "Geopolitics":  r"\b(war|attack|missile|military|invasion|conflict|sanction|nato|russia|ukraine|israel|iran|china|taiwan|north korea|ceasefire)\b",
    "Weather/Geo":  r"\b(earthquake|hurricane|tornado|flood|tsunami|storm|cyclone|eruption|volcano|wildfire|disaster|quake|magnitude)\b",
    "Finance/Econ": r"\b(fed|rate|inflation|gdp|recession|s&p|nasdaq|dow|stock|market|interest|cpi|unemployment|yield|bond)\b",
    "AI/Tech":      r"\b(ai|gpt|openai|anthropic|claude|gemini|llm|model|nvidia|apple|google|meta|microsoft|tech|launch|release)\b",
    "Celebs/Pop":   r"\b(oscar|grammy|emmy|taylor|swift|kanye|kardashian|hollywood|music|album|movie|film|award|celebrity)\b",
}

def classify(title: str) -> str:
    t = title.lower()
    for cat, pattern in CATEGORY_PATTERNS.items():
        if re.search(pattern, t, re.IGNORECASE):
            return cat
    return "Other"


# ── fetch trades ───────────────────────────────────────────────────────────

def fetch_trades():
    print("\n[1/3] Fetching trade history…")
    # /trades?user= correctly filters by wallet; maker= and taker= are ignored by the API
    trades = paginate(f"{DATA_API}/trades", params={"user": WALLET}, limit=500)
    print(f"  Total unique trades fetched: {len(trades)}")
    return trades


def fetch_positions():
    print("\n[2/3] Fetching open positions…")
    # user= query param correctly filters by wallet on this endpoint
    positions = paginate(f"{DATA_API}/positions", params={"user": WALLET, "sizeThreshold": "0"}, limit=500)
    print(f"  Total positions fetched: {len(positions)}")
    return positions


# ── analyse ────────────────────────────────────────────────────────────────

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def analyze_trades(trades, positions):
    print("\n[3/3] Analysing…")

    # ── basic P&L from positions (more reliable than raw trades) ──────────
    cat_stats = defaultdict(lambda: {"count": 0, "invested": 0.0, "pnl": 0.0,
                                     "wins": 0, "losses": 0, "biggest_win": 0.0})
    total_invested = 0.0
    total_pnl = 0.0
    wins = losses = 0
    entry_prices = []
    pnl_by_month = defaultdict(float)

    for pos in positions:
        title = pos.get("title") or pos.get("market", {}).get("question") or pos.get("question") or ""
        invested = safe_float(pos.get("investedAmount") or pos.get("cost"))
        pnl      = safe_float(pos.get("realizedPnl") or pos.get("pnl") or 0)
        entry_p  = safe_float(pos.get("entryPrice") or pos.get("avgPrice") or 0)
        ts_raw   = pos.get("closedAt") or pos.get("timestamp") or pos.get("createdAt") or ""

        cat = classify(title)
        s   = cat_stats[cat]
        s["count"]     += 1
        s["invested"]  += invested
        s["pnl"]       += pnl

        if pnl > 0:
            s["wins"] += 1
            wins += 1
            if pnl > s["biggest_win"]:
                s["biggest_win"] = pnl
        elif pnl < 0:
            s["losses"] += 1
            losses += 1

        total_invested += invested
        total_pnl      += pnl
        if entry_p:
            entry_prices.append(entry_p)

        # monthly breakdown
        try:
            month = datetime.fromisoformat(str(ts_raw).replace("Z", "")).strftime("%Y-%m")
            pnl_by_month[month] += pnl
        except Exception:
            pass

    # ── price-range buckets from raw trades ───────────────────────────────
    bucket_stats = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})

    for pos in positions:
        price = safe_float(pos.get("entryPrice") or pos.get("avgPrice") or 0)
        pnl   = safe_float(pos.get("realizedPnl") or pos.get("pnl") or 0)
        if price <= 0:
            continue
        if price < 0.005:
            bucket = "<0.5¢"
        elif price < 0.01:
            bucket = "0.5–1¢"
        elif price < 0.02:
            bucket = "1–2¢"
        elif price < 0.05:
            bucket = "2–5¢"
        elif price < 0.10:
            bucket = "5–10¢"
        else:
            bucket = ">10¢"
        b = bucket_stats[bucket]
        b["count"] += 1
        b["pnl"]   += pnl
        if pnl > 0:
            b["wins"] += 1

    return {
        "total_positions": len(positions),
        "total_trades": len(trades),
        "total_invested": total_invested,
        "total_pnl": total_pnl,
        "total_return_pct": (total_pnl / total_invested * 100) if total_invested else 0,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / (wins + losses) * 100 if (wins + losses) else 0,
        "avg_entry_price": sum(entry_prices) / len(entry_prices) if entry_prices else 0,
        "category_stats": dict(cat_stats),
        "bucket_stats": dict(bucket_stats),
        "pnl_by_month": dict(sorted(pnl_by_month.items())),
    }


# ── pretty print ──────────────────────────────────────────────────────────

def print_report(stats):
    try:
        from tabulate import tabulate
        HAS_TAB = True
    except ImportError:
        HAS_TAB = False

    sep = "=" * 64
    print(f"\n{sep}")
    print("  planktonXD — Polymarket Trade Analysis")
    print(f"  Wallet: {WALLET}")
    print(sep)

    print(f"\n{'OVERVIEW':─<40}")
    print(f"  Total positions analysed : {stats['total_positions']:>8,}")
    print(f"  Total raw trades fetched : {stats['total_trades']:>8,}")
    print(f"  Total invested           : ${stats['total_invested']:>10,.2f}")
    print(f"  Total P&L                : ${stats['total_pnl']:>+10,.2f}")
    print(f"  Overall return           : {stats['total_return_pct']:>+8.1f}%")
    print(f"  Win rate                 : {stats['win_rate']:>8.1f}%")
    print(f"  Wins / Losses            : {stats['wins']:,} / {stats['losses']:,}")
    print(f"  Avg entry price          : ${stats['avg_entry_price']:.4f}")

    # ── category table ────────────────────────────────────────────────────
    print(f"\n{'BY CATEGORY':─<40}")
    cat = stats["category_stats"]
    rows = []
    for c, s in sorted(cat.items(), key=lambda x: -x[1]["pnl"]):
        wr = s["wins"] / (s["wins"] + s["losses"]) * 100 if (s["wins"] + s["losses"]) else 0
        roi = s["pnl"] / s["invested"] * 100 if s["invested"] else 0
        rows.append([c, s["count"], f"${s['invested']:.0f}",
                     f"${s['pnl']:+.0f}", f"{roi:+.0f}%", f"{wr:.0f}%",
                     f"${s['biggest_win']:.2f}"])
    headers = ["Category", "Pos.", "Invested", "P&L", "ROI", "Win%", "Biggest win"]
    if HAS_TAB:
        print(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        print("  " + " | ".join(headers))
        for r in rows:
            print("  " + " | ".join(str(x) for x in r))

    # ── price bucket table ────────────────────────────────────────────────
    print(f"\n{'BY ENTRY PRICE BUCKET':─<40}")
    bkt = stats["bucket_stats"]
    rows2 = []
    order = ["<0.5¢", "0.5–1¢", "1–2¢", "2–5¢", "5–10¢", ">10¢"]
    for b in order:
        if b not in bkt:
            continue
        s = bkt[b]
        wr = s["wins"] / s["count"] * 100 if s["count"] else 0
        rows2.append([b, s["count"], f"${s['pnl']:+.2f}", f"{wr:.0f}%"])
    headers2 = ["Price bucket", "Count", "P&L", "Win%"]
    if HAS_TAB:
        print(tabulate(rows2, headers=headers2, tablefmt="simple"))
    else:
        print("  " + " | ".join(headers2))
        for r in rows2:
            print("  " + " | ".join(str(x) for x in r))

    # ── monthly P&L ───────────────────────────────────────────────────────
    if stats["pnl_by_month"]:
        print(f"\n{'MONTHLY P&L':─<40}")
        for month, pnl in stats["pnl_by_month"].items():
            bar = "█" * int(abs(pnl) / 200)
            sign = "+" if pnl >= 0 else ""
            print(f"  {month}  {sign}${pnl:,.0f}  {bar}")

    print(f"\n{sep}\n")


# ── main ──────────────────────────────────────────────────────────────────

def main():
    print("Polymarket planktonXD Analyzer")
    print(f"Wallet: {WALLET}\n")

    trades    = fetch_trades()
    positions = fetch_positions()

    if not positions and not trades:
        print("\n[!] Could not fetch any data. Check your internet connection and try again.")
        print("    Endpoints tried:")
        print(f"      {DATA_API}/trades?maker={WALLET}")
        print(f"      {DATA_API}/positions?user={WALLET}")
        return

    stats = analyze_trades(trades, positions)
    print_report(stats)

    # save raw data for further analysis
    with open("plankton_trades.json", "w") as f:
        json.dump(trades, f, indent=2)
    with open("plankton_positions.json", "w") as f:
        json.dump(positions, f, indent=2)
    print("Raw data saved to plankton_trades.json and plankton_positions.json")


if __name__ == "__main__":
    main()
