#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
import requests

BASE_URL = "https://webapi.legistar.com/v1/nyc"
TOKEN = os.environ.get("LEGISTAR_TOKEN", "").strip()
YEAR = os.environ.get("TARGET_YEAR", "2026")

# Map Council Member last names / full names to District IDs (1-51)
LAST_NAME_TO_DISTRICT = {
    "Marte": 1, "Harvey": 2, "Bottcher": 3, "Maloney": 4, "Menin": 5,
    "Brewer": 6, "Abreu": 7, "Elsayed": 8, "Salaam": 9, "De La Rosa": 10,
    "Dinowitz": 11, "Riley": 12, "farias": 13, "Farías": 13, "Sanchez": 14,
    "Feliz": 15, "Stevens": 16, "Encarnacion": 17, "utano": 18, "Paladino": 19,
    "Ung": 20, "Shanks": 21, "Cabán": 22, "Caban": 22, "Lee": 23,
    "Gennaro": 24, "Krishnan": 25, "Won": 26, "Williams": 27, "Singh": 28,
    "Schulman": 29, "wong": 30, "Brooks-Powers": 31, "Ariola": 32, "Restler": 33,
    "Gutiérrez": 34, "Gutierrez": 34, "Hudson": 35, "Osse": 36, "Ossé": 36,
    "Nurse": 37, "Avilés": 38, "Aviles": 38, "Hanif": 39, "Joseph": 40,
    "Mealy": 41, "Banks": 42, "Zhuang": 43, "Tischler": 44, "Louis": 45,
    "Narcisse": 46, "Santosuosso": 47, "Vernikov": 48, "Hanks": 49,
    "Carr": 50, "Morano": 51
}

def district_by_person_id():
    """PersonId -> district, from the council.nyc.gov/district-N/ (or legacy /dN/) link on each
    person's record, including former members so past votes map correctly. People whose link is
    a name slug are covered by data/district_overrides.json."""
    out, skip = {}, 0
    while True:
        page = api_get("persons", {"$top": 1000, "$skip": skip})
        for p in page:
            m = re.search(r"council\.nyc\.gov/(?:district-|d)(\d{1,2})(?:/|$)", p.get("PersonWWW") or "")
            if m and 1 <= int(m.group(1)) <= 51:
                out[p["PersonId"]] = int(m.group(1))
        if len(page) < 1000:
            break
        skip += 1000
    overrides = Path("data/district_overrides.json")
    if overrides.exists():
        out.update({int(k): v for k, v in json.loads(overrides.read_text()).items() if not k.startswith("_")})
    return out

PERSON_DISTRICTS = {}

REPUBLICAN_CONFERENCE_BODY_ID = 5264

def republican_person_ids():
    """Legistar has no party field; membership in the Minority (Republican) Conference is the signal."""
    recs = api_get("officerecords", {"$filter": f"OfficeRecordBodyId eq {REPUBLICAN_CONFERENCE_BODY_ID} and OfficeRecordEndDate ge datetime'{YEAR}-01-01'"})
    return {r["OfficeRecordPersonId"] for r in recs}

def api_get(endpoint, params=None):
    params = params or {}
    if TOKEN:
        params["token"] = TOKEN
    for attempt in range(5):  # the API occasionally resets connections or returns 5xx
        try:
            resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=60)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(f"{resp.status_code} for {endpoint}")
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            if attempt == 4 or (isinstance(e, requests.HTTPError) and "for " not in str(e)):
                raise
            time.sleep(2 ** attempt)

def _fold(text):
    """Lowercase and strip accents so 'Farías' == 'farias'."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()

# Match the surname at the end of the name, so "Joseph" in "Joseph Borelli" or "Lee" in "Fleetwood" can't hit.
_PATTERNS = [(re.compile(r"\b" + re.escape(_fold(k)) + r"$"), d) for k, d in LAST_NAME_TO_DISTRICT.items()]
# Non-voting Public Advocate shares a surname with District 27's member
NOT_DISTRICT_MEMBERS = ("jumaane",)
UNMATCHED = set()

def match_district(person_name, person_id=None):
    if person_id in PERSON_DISTRICTS:
        return PERSON_DISTRICTS[person_id]
    name = re.sub(r"\s+(jr|sr|ii|iii)\.?$", "", _fold(person_name).strip())
    if any(n in name for n in NOT_DISTRICT_MEMBERS):
        return None
    for pat, dist_id in _PATTERNS:
        if pat.search(name):
            return dist_id
    UNMATCHED.add(person_name)
    return None

def map_vote_value(val_str):
    if not val_str:
        return "excused"
    v = val_str.lower()
    if "affirmative" in v or v == "yes":
        return "yes"
    if "negative" in v or v == "no":
        return "no"
    return "excused"

# Shared state, set in main()
MEMBERS_OUT = {}      # district -> {name, party}, collected from vote records (latest wins)
REPUBLICANS = set()
PARTY_OVERRIDES = {}

def votes_for_item(event_item_id):
    """District -> yes/no/excused for one agenda item; also records member names/parties."""
    out = {}
    for v in api_get(f"eventitems/{event_item_id}/votes"):
        person_name = v.get("VotePersonName", "")
        dist_id = match_district(person_name, v.get("VotePersonId"))
        if dist_id:
            party = PARTY_OVERRIDES.get(str(dist_id)) or ("R" if v.get("VotePersonId") in REPUBLICANS else "D")
            MEMBERS_OUT[str(dist_id)] = {"name": person_name.strip(), "party": party, "personId": v.get("VotePersonId")}
            out[str(dist_id)] = map_vote_value(v.get("VoteValueName"))
    return out

def _is_council_approval(h):
    name = (h.get("MatterHistoryActionName") or "").lower()
    return "approved" in name and "by council" in name and h.get("MatterHistoryEventId")

def build_vetoed(events):
    """Bills the Mayor vetoed: still-vetoed matters plus Council overrides in `events`.
    date = veto date; votes = the Council vote the Mayor vetoed; overrideVotes = override vote if any."""
    found = {}  # matter_id -> {"override_item": item | None}
    for m in api_get("matters", {"$filter": "MatterStatusName eq 'Vetoed'"}):
        found[m["MatterId"]] = {"override_item": None}
    for ev in events:
        for item in api_get(f"events/{ev['EventId']}/eventitems"):
            if (item.get("EventItemActionName") or "").lower().startswith("overridden") and item.get("EventItemMatterId"):
                found[item["EventItemMatterId"]] = {"override_item": item, "override_date": ev["EventDate"][:10]}

    out = []
    for mid, info in found.items():
        matter = api_get(f"matters/{mid}")
        hist = api_get(f"matters/{mid}/histories")
        vetoes = sorted(h["MatterHistoryActionDate"][:10] for h in hist
                        if (h.get("MatterHistoryActionName") or "").lower().startswith("vetoed"))
        if not vetoes:
            continue
        veto_date = vetoes[-1]
        approvals = sorted((h for h in hist if _is_council_approval(h) and h["MatterHistoryActionDate"][:10] <= veto_date),
                           key=lambda h: h["MatterHistoryActionDate"])
        votes = {}
        for h in reversed(approvals):  # latest Council approval before the veto
            for it in api_get(f"events/{h['MatterHistoryEventId']}/eventitems"):
                if it.get("EventItemMatterId") == mid and it.get("EventItemPassedFlag") == 1:
                    votes = votes_for_item(it["EventItemId"])
                    break
            if votes:
                break
        if not votes:
            print(f"skip {matter.get('MatterFile')}: no Council vote records found", file=sys.stderr)
            continue
        entry = {
            "id": matter["MatterFile"],
            "date": veto_date,
            "title": matter.get("MatterName") or matter.get("MatterTitle") or matter["MatterFile"],
            "category": matter.get("MatterTypeName") or "Legislation",
            "summary": matter.get("MatterTitle") or "",
            "legistarUrl": f"https://legistar.council.nyc.gov/gateway.aspx?M=L&ID={mid}",
            "status": "Overridden" if info["override_item"] else "Vetoed",
            "votes": votes,
        }
        if info["override_item"]:
            ov = votes_for_item(info["override_item"]["EventItemId"])
            if ov:
                entry["overrideDate"] = info["override_date"]
                entry["overrideVotes"] = ov
        out.append(entry)
    return sorted(out, key=lambda b: (b["date"], b["id"]))

def main():
    # 1. Fetch City Council Stated Meetings (BodyId 1 is City Council) for the target year
    odata_filter = (
        f"EventBodyId eq 1 and "
        f"EventDate ge datetime'{YEAR}-01-01' and "
        f"EventDate le datetime'{YEAR}-12-31'"
    )
    events = api_get("events", {"$filter": odata_filter, "$orderby": "EventDate asc"})
    
    bills_output = []
    members = MEMBERS_OUT
    PERSON_DISTRICTS.update(district_by_person_id())
    REPUBLICANS.update(republican_person_ids())
    po = Path("data/party_overrides.json")  # {"<district>": "I"} for members who are neither R nor D
    if po.exists():
        PARTY_OVERRIDES.update({k: v for k, v in json.loads(po.read_text()).items() if not k.startswith("_")})
    
    for ev in events:
        event_id = ev["EventId"]
        event_date = ev["EventDate"][:10] # YYYY-MM-DD
        items = api_get(f"events/{event_id}/eventitems")
        
        for item in items:
            # Approved legislation (RollCallFlag is unreliable: approved items carry 0, so we
            # rely on the votes endpoint returning records instead)
            if item.get("EventItemPassedFlag") == 1:
                matter_file = item.get("EventItemMatterFile") or ""
                if not matter_file.startswith(("Int", "Res")):
                    continue
                
                event_item_id = item["EventItemId"]
                district_votes = votes_for_item(event_item_id)
                
                if district_votes:
                    bills_output.append({
                        "id": matter_file,
                        "date": event_date,
                        "title": item.get("EventItemMatterName") or item.get("EventItemTitle") or matter_file,
                        "category": item.get("EventItemMatterType") or "Legislation",
                        "summary": item.get("EventItemTitle") or "",
                        "legistarUrl": f"https://legistar.council.nyc.gov/gateway.aspx?M=L&ID={item.get('EventItemMatterId')}",  # gateway redirects to the detail page; API MatterId != web ID,
                        "votes": district_votes
                    })

    members = dict(MEMBERS_OUT)  # snapshot: current members only, before older vetoed-bill votes are read
    vetoed = build_vetoed(events)

    out_dir = Path("data")
    (out_dir).mkdir(exist_ok=True)
    with open(out_dir / "members.json", "w", encoding="utf-8") as f:
        json.dump(dict(sorted(members.items(), key=lambda kv: int(kv[0]))), f, indent=2, ensure_ascii=False)
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "votes.json", "w", encoding="utf-8") as f:
        json.dump(bills_output, f, indent=2, ensure_ascii=False)
    with open(out_dir / "vetoed.json", "w", encoding="utf-8") as f:
        json.dump(vetoed, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(vetoed)} vetoed bills to data/vetoed.json")
    if UNMATCHED:
        print("WARNING: no district for:", ", ".join(sorted(UNMATCHED)), file=sys.stderr)
    print(f"Saved {len(bills_output)} approved bills to data/votes.json")

if __name__ == "__main__":
    main()
