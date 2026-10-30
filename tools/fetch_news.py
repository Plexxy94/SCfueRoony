"""Sammelt News für den News-Tab und schreibt news.json.

Quellen:
- RSI-Server-Status (status.robertsspaceindustries.com/index.json)
- Offizieller YouTube-Kanal (RSS-Feed)
- Executive-Hangar-Timer: Startzeit des Zyklus von contestedzonetimers.com (Rückfall grimhex.net)
- RSI Comm-Link: Liste direkt von RSI, genaues Datum aus der Star Citizen Wiki API (nur Titel, Datum, Link).
  Die Wiki hängt manchmal Tage hinterher; Beiträge, die sie noch nicht kennt, bekommen ein geschätztes Datum ("approx").

Läuft stündlich per GitHub Actions. Schreibt die Datei nur neu, wenn sich der Inhalt geändert hat.
Nur Standardbibliothek, damit der Job nichts installieren muss.
"""
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "SC-Handbuch-News/1.0 (non-commercial fan guide; github.com/Plexxy94/SCfueRoony)"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def status():
    d = json.loads(get("https://status.robertsspaceindustries.com/index.json"))
    return {
        "summary": d.get("summaryStatus"),
        "systems": [{"name": s.get("name"), "status": s.get("status")} for s in d.get("systems") or []],
        "url": "https://status.robertsspaceindustries.com/",
    }


def videos(limit=10):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(get("https://www.youtube.com/feeds/videos.xml?user=RobertsSpaceInd"))
    out = []
    for e in root.findall("a:entry", ns)[:limit]:
        link = e.find("a:link", ns)
        out.append({
            "title": (e.findtext("a:title", default="", namespaces=ns) or "").strip(),
            "url": link.get("href") if link is not None else None,
            "date": e.findtext("a:published", default="", namespaces=ns),
        })
    return out


def wiki_commlinks(cutoff, max_pages=4):
    """Comm-Links mit genauem Datum aus der Star Citizen Wiki API (hängt manchmal Tage hinterher)."""
    out = []
    for page in range(1, max_pages + 1):
        d = json.loads(get(f"https://api.star-citizen.wiki/api/v2/comm-links?limit=30&page={page}"))
        rows = d.get("data") or []
        for c in rows:
            out.append({"id": c.get("id"), "title": c.get("title"), "url": c.get("rsi_url"),
                        "date": c.get("created_at"), "channel": c.get("channel"), "category": c.get("category"),
                        "series": c.get("series")})
        if not rows or min(datetime.fromisoformat(c["created_at"]) for c in rows) < cutoff:
            break
    return out


def rsi_commlinks(pages=3):
    """Aktuelle Liste direkt von RSI. Enthält kein genaues Datum, nur „vor X Tagen“."""
    out = []
    for page in range(1, pages + 1):
        body = json.dumps({"channel": "", "series": "", "type": "", "text": "", "sort": "publish_new", "page": page}).encode()
        req = urllib.request.Request("https://robertsspaceindustries.com/api/hub/getCommlinkItems", data=body,
                                     headers={**UA, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        blocks = re.findall(r'href="(/comm-link/[^"]+)".*?<div class="title(?:\s[^"]*)?">(.*?)</div>.*?<span class="value">(.*?)</span>',
                            d.get("data") or "", re.S)
        if not blocks:
            break
        for href, title, ago in blocks:
            m = re.search(r"/(\d+)-", href)
            if m:
                out.append({"id": int(m.group(1)), "title": html.unescape(title).strip(),
                            "url": "https://robertsspaceindustries.com/en" + href, "ago": ago.strip()})
    return out


MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}


def guess_date(title, ago, now):
    """Datum aus dem Titel („October 26, 2026“) oder grob aus „4 days ago“."""
    m = re.search(r"(" + "|".join(MONTHS) + r") (\d{1,2}), (\d{4})", title, re.I)
    if m:
        return datetime(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)), 12, tzinfo=timezone.utc)
    m = re.match(r"(\d+|an?|one)\s+(minute|hour|day|week|month)s?\s+ago", ago, re.I)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else 1
        return now - timedelta(days=n * {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30}[m.group(2).lower()])
    return now


def commlinks(prev, days=40):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    wiki, rsi = [], []
    try:
        wiki = wiki_commlinks(cutoff)
    except Exception as exc:
        print(f"Wiki: Fehler {exc}", file=sys.stderr)
    try:
        rsi = rsi_commlinks()
    except Exception as exc:
        print(f"RSI: Fehler {exc}", file=sys.stderr)
    if not wiki and not rsi:
        raise RuntimeError("keine Quelle erreichbar")
    by_id = {c["id"]: c for c in wiki}
    # Beiträge, die die Wiki noch nicht kennt; ein einmal geschätztes Datum bleibt stehen, damit die Datei nicht stündlich wechselt
    old = {c["id"]: c["date"] for c in prev if c.get("approx")}
    for c in rsi:
        if c["id"] not in by_id:
            date = old.get(c["id"]) or guess_date(c["title"], c["ago"], now).isoformat(timespec="seconds")
            by_id[c["id"]] = {"id": c["id"], "title": c["title"], "url": c["url"], "date": date, "approx": True}
    out = [c for c in by_id.values() if c["date"] and datetime.fromisoformat(c["date"]) >= cutoff]
    out.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    return out


def exec_timer():
    """Startzeit des 185-Minuten-Zyklus der Executive Hangars (Beginn der roten Ladephase, Unix-Sekunden).
    Quelle: contestedzonetimers.com, Rückfall: grimhex.net (dort ist der Anker der Öffnungszeitpunkt)."""
    try:
        return {"start": int(get("https://contestedzonetimers.com/lib/cfg.dat").decode().strip()), "src": "contestedzonetimers.com"}
    except Exception as exc:
        print(f"exec (contestedzonetimers): Fehler {exc}", file=sys.stderr)
    m = re.search(r'serverAnchor\\?":(\d{13})', get("https://grimhex.net/tools/exec-hangar").decode("utf-8", "replace"))
    if not m:
        raise RuntimeError("kein Anker gefunden")
    return {"start": int(m.group(1)) // 1000 - 2 * 3600, "src": "grimhex.net"}


def main(path):
    try:
        old = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    new = {}
    for key, fn in (("status", status), ("videos", videos), ("commlinks", lambda: commlinks(old.get("commlinks") or [])), ("exec", exec_timer)):
        try:
            new[key] = fn()
        except Exception as exc:  # eine Quelle darf ausfallen, die anderen bleiben aktuell
            print(f"{key}: Fehler {exc}", file=sys.stderr)
    for key in ("status", "videos", "commlinks", "exec"):
        if key not in new and key in old:
            new[key] = old[key]
    if {k: old.get(k) for k in new} == new:
        print("unverändert")
        return
    new["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(new, fh, ensure_ascii=False, indent=1)
    print("aktualisiert")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "news.json")
