"""Sammelt Raffinerie-Boni und Verkaufspreise je Erz von UEX und schreibt mining.json.

Quellen (UEX API 2.0, Spielerberichte):
- refineries_yields: Ausbeute-Bonus oder -Malus in Prozent je Raffinerie und Erz
- commodities_prices_all: Ankaufspreise je Terminal, roh und raffiniert

Läuft zusammen mit fetch_news.py stündlich per GitHub Actions und schreibt nur bei Änderungen.
Nur Standardbibliothek.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "SC-Handbuch-Mining/1.0 (non-commercial fan guide; github.com/Plexxy94/SCfueRoony)"}
API = "https://api.uexcorp.space/2.0/"


def get(path):
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=UA), timeout=60) as r:
        d = json.load(r)
    if d.get("status") != "ok":
        raise RuntimeError(f"{path}: {d.get('status')}")
    return d.get("data") or []


def key(name):
    """'Gold (Ore)' / 'Quantainium (Raw)' / 'Gold' -> 'gold'"""
    return re.sub(r"\s*\((ore|raw)\)\s*$", "", name or "", flags=re.I).strip().lower()


def station(row):
    n = row.get("space_station_name") or row.get("city_name") or row.get("terminal_name") or ""
    n = re.sub(r"^Refinement Center - ", "", n)
    # "MIC-L2 Long Forest Station" -> "MIC-L2"
    m = re.match(r"^([A-Z]{3}-L\d)\b", n)
    return m.group(1) if m else n


def main(path):
    comms = get("commodities")
    ores = {}  # Schlüssel -> {"raw": id, "ref": id}
    for c in comms:
        n = c.get("name") or ""
        if c.get("is_raw") and re.search(r"\((ore|raw)\)$", n, re.I):
            ores.setdefault(key(n), {})["raw"] = c["id"]
    for c in comms:
        k = key(c.get("name"))
        if k in ores and c.get("is_refined") and not re.search(r"\((ore|raw)\)$", c.get("name") or "", re.I):
            ores[k]["ref"] = c["id"]

    yields = {}
    for y in get("refineries_yields"):
        k = key(y.get("commodity_name"))
        if k not in ores or y.get("value") is None:
            continue
        yields.setdefault(k, []).append([station(y), y.get("star_system_name"), y["value"]])
    for v in yields.values():
        v.sort(key=lambda x: (-x[2], x[0]))

    by_id = {}
    for p in get("commodities_prices_all"):
        if p.get("price_sell"):
            by_id.setdefault(p["id_commodity"], []).append([p["price_sell"], p.get("terminal_name")])
    sell = {}
    for k, ids in ores.items():
        e = {}
        for kind in ("ref", "raw"):
            rows = sorted(by_id.get(ids.get(kind), []), key=lambda x: -x[0])[:5]
            if rows:
                e[kind] = rows
        if e:
            sell[k] = e

    new = {"yields": dict(sorted(yields.items())), "sell": dict(sorted(sell.items()))}
    try:
        old = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if {k: old.get(k) for k in new} == new:
        print("unverändert")
        return
    new["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(new, fh, ensure_ascii=False, indent=1)
    print("aktualisiert:", len(yields), "Erze mit Raffinerie-Daten,", len(sell), "mit Preisen")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "mining.json")
