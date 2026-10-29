"""Sammelt News für den News-Tab und schreibt news.json.

Quellen:
- RSI-Server-Status (status.robertsspaceindustries.com/index.json)
- Offizieller YouTube-Kanal (RSS-Feed)
- RSI Comm-Link über die Star Citizen Wiki API (nur Titel, Datum, Link; als Rückfall, falls der Live-Abruf im Browser scheitert)

Läuft stündlich per GitHub Actions. Schreibt die Datei nur neu, wenn sich der Inhalt geändert hat.
Nur Standardbibliothek, damit der Job nichts installieren muss.
"""
import json
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


def commlinks(days=40, max_pages=4):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
    out = [c for c in out if c["date"] and datetime.fromisoformat(c["date"]) >= cutoff]
    out.sort(key=lambda c: c["date"], reverse=True)
    return out


def main(path):
    new = {}
    for key, fn in (("status", status), ("videos", videos), ("commlinks", commlinks)):
        try:
            new[key] = fn()
        except Exception as exc:  # eine Quelle darf ausfallen, die anderen bleiben aktuell
            print(f"{key}: Fehler {exc}", file=sys.stderr)
    try:
        old = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    for key in ("status", "videos", "commlinks"):
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
