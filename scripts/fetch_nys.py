#!/usr/bin/env python3
"""Build data/state/*.json for the NY State Senate view.

Sources
  NY Senate Open Legislation API (https://legislation.nysenate.gov/): bills, Senate floor roll calls,
      members, committees. Needs NYS_LEGISLATION_KEY.
  Wikipedia "New York State Senate" (Current members + leadership tables): party and leadership roles.
  Wikidata / Wikimedia Commons: birth date and photo (same verified-match approach as the NYC profiles).
Only Senate roll calls exist in the API; Assembly votes are not available from it.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from fetch_profiles import wikidata_people, match_wikidata, commons, WD_UA

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "state"
KEY = os.environ.get("NYS_LEGISLATION_KEY", "").strip()
SESSION = int(os.environ.get("NYS_SESSION", "2025"))
API = "https://legislation.nysenate.gov/api/3"
YES = {"AYE", "AYEWR"}


def cut(s, n=400):
    return s if len(s) <= n else s[:n - 3].rsplit(" ", 1)[0] + "…"


def api(path, **params):
    params["key"] = KEY
    for attempt in range(5):
        try:
            r = requests.get(API + path, params=params, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            d = r.json()
            if not d.get("success", True):
                raise RuntimeError(d.get("message"))
            return d
        except (requests.RequestException, ValueError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def current_members():
    out, offset = {}, 1
    while True:
        d = api(f"/members/{SESSION}", limit=200, offset=offset, full="true")
        for m in d["result"]["items"]:
            if m.get("chamber") == "SENATE" and m.get("sessionYear") == SESSION:
                cur = out.get(m["districtCode"])
                # prefer the member flagged incumbent; the flag can lag, so fall back to any session member
                if cur is None or (m.get("incumbent") and not cur.get("incumbent")):
                    out[m["districtCode"]] = m
        if d["offsetEnd"] >= d["total"] or not d["result"]["items"]:
            return out
        offset = d["offsetEnd"] + 1


def wiki_tables():
    """district -> party, district -> [leadership roles] from Wikipedia's Senate article."""
    r = requests.get("https://en.wikipedia.org/w/api.php", headers={"User-Agent": WD_UA}, timeout=60, params={
        "action": "parse", "page": "New_York_State_Senate", "prop": "wikitext", "format": "json", "formatversion": 2})
    r.raise_for_status()
    t = r.json()["parse"]["wikitext"]
    party, roles = {}, {}
    head = t.find("== Current members ==")
    for m in re.finditer(r"\n!\[\[New York's (\d+)(?:st|nd|rd|th) State Senate district\|\d+\]\][^\n]*\n(?:[^\n]*\n){1,3}?\|\{\{[Pp]arty shading/[^}]*\}\} ?\|([^\n]+)", t[head:]):
        party[int(m.group(1))] = m.group(2).strip()
    for m in re.finditer(r"\n!([^\n]+)\n\|[^\n]*\n\|\s*\{\{[Pp]arty shading/[^\n]*\n\|\s*(\d{1,2})\s*\n", t[:head]):
        title = re.sub(r"<ref.*", "", re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", m.group(1))).replace("scope=\"row\" |", "").replace("scope=row|", "").strip()
        roles.setdefault(int(m.group(2)), []).append(title)
    return party, roles


def party_letter(name):
    n = (name or "").lower()
    return "R" if "republican" in n else "D" if "democrat" in n else "I" if n else ""


def fetch_bill(item):
    b = item["result"]
    return api(f"/bills/{b['session']}/{b['basePrintNo']}")["result"]


def build_bills():
    ids, offset = [], 1
    while True:
        d = api("/bills/search", term=f"session:{SESSION} AND votes.items.voteType:FLOOR", limit=1000, offset=offset)
        ids += d["result"]["items"]
        if d["offsetEnd"] >= d["total"] or not d["result"]["items"]:
            break
        offset = d["offsetEnd"] + 1
    print(f"{len(ids)} bills with floor votes; fetching details", file=sys.stderr)
    with ThreadPoolExecutor(6) as ex:
        bills = list(ex.map(fetch_bill, ids))
    approved, vetoed = [], []
    for b in bills:
        floors = [v for v in b["votes"]["items"] if v["voteType"] == "FLOOR"]
        if not floors:
            continue
        v = max(floors, key=lambda v: v["voteDate"])
        votes, ayes = {}, 0
        for code, grp in v["memberVotes"]["items"].items():
            for m in grp["items"]:
                votes[str(m["districtCode"])] = "yes" if code in YES else "no" if code == "NAY" else "excused"
                ayes += code in YES
        if ayes < 32:
            continue
        pn = b["basePrintNo"]
        rec = {"id": pn, "date": v["voteDate"][:10], "title": b["title"],
               "category": "Resolution" if b["billType"].get("resolution") else "Bill",
               "summary": cut((b.get("summary") or b["title"]).strip()),
               "legistarUrl": f"https://www.nysenate.gov/legislation/bills/{b['session']}/{pn}",
               "votes": votes}
        sm = [m for m in b["milestones"]["items"] if m["statusType"] == "SIGNED_BY_GOV"]
        if sm:
            rec["signedDate"] = sm[-1]["actionDate"]
        approved.append(rec)
        vm = [m for m in b["milestones"]["items"] if m["statusType"] == "VETOED"]
        if vm:
            vetoed.append(dict(rec, date=vm[-1]["actionDate"], status="Vetoed"))
    key = lambda r: (r["date"], r["id"])
    return sorted(approved, key=key), sorted(vetoed, key=key)


def main():
    if not KEY:
        sys.exit("NYS_LEGISLATION_KEY is not set")
    OUT.mkdir(parents=True, exist_ok=True)
    members = current_members()
    party_by_d, roles_by_d = wiki_tables()
    if len(party_by_d) >= 55:  # Wikipedia marks vacant seats "not applicable"; the API's member list can lag
        for d in [d for d in members if d not in party_by_d]:
            print(f"District {d}: vacant per Wikipedia ({members[d]['fullName']} not listed)", file=sys.stderr)
            del members[d]
    if os.environ.get("REUSE_BILLS"):  # local shortcut: keep the existing bill files
        approved = json.loads((OUT / "votes.json").read_text())
        vetoed = json.loads((OUT / "vetoed.json").read_text())
    else:
        approved, vetoed = build_bills()
    (OUT / "votes.json").write_text(json.dumps(approved, separators=(",", ":")))
    (OUT / "vetoed.json").write_text(json.dumps(vetoed, separators=(",", ":")))
    print(f"Saved {len(approved)} passed bills, {len(vetoed)} vetoed", file=sys.stderr)

    out_members = {str(d): {"name": m["fullName"], "party": party_letter(party_by_d.get(d)), "personId": m["memberId"]}
                   for d, m in sorted(members.items())}
    (OUT / "members.json").write_text(json.dumps(out_members, indent=1))
    if len(members) < 62:
        print(f"WARNING: {len(members)} current senators found (expected 63)", file=sys.stderr)

    # committees
    comm = {d: [] for d in members}
    for c in api(f"/committees/{SESSION}/SENATE", full="true")["result"]["items"]:
        for m in c["committeeMembers"]["items"]:
            if m["districtCode"] in comm:
                comm[m["districtCode"]].append({"name": c["name"], "role": "Chair" if m["title"] == "CHAIR_PERSON" else ""})

    people = []
    try:
        people = wikidata_people([m["fullName"] for m in members.values()], position="New York State Senate")
    except Exception as e:  # profiles still ship without photos/ages
        print(f"Wikidata lookup failed: {e}", file=sys.stderr)
    profiles, missing = {}, []
    for d, m in sorted(members.items()):
        w = match_wikidata(m["fullName"], people) or {}
        pl = party_by_d.get(d, "")
        conf = {"R": "Republican Conference", "D": "Democratic Conference"}.get(party_letter(pl))
        years = (m.get("service") or {}).get("sessionYearsServed") or []
        prof = {"name": m["fullName"], "party": party_letter(pl),
                "tookOffice": min(years) if years else None,
                "roles": roles_by_d.get(d, []), "caucuses": [conf] if conf else [],
                "committees": sorted(comm[d], key=lambda c: (c["role"] == "", c["name"]))}
        if "birth" in w:
            prof["birth"] = w["birth"]
        if "img" in w:
            prof.update(commons(w["img"]))
        profiles[str(d)] = prof
        if "img" not in w or "birth" not in w:
            missing.append(f"D{d} {m['fullName']}")
    (OUT / "profiles.json").write_text(json.dumps(profiles, indent=1))
    print(f"Saved {len(profiles)} profiles ({sum('photo' in p for p in profiles.values())} photos, "
          f"{sum('birth' in p for p in profiles.values())} birth dates)", file=sys.stderr)
    print("Missing:", "; ".join(missing[:12]), file=sys.stderr)


if __name__ == "__main__":
    main()
