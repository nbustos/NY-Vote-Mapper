#!/usr/bin/env python3
"""Build data/profiles.json: per-district member profile for the map's detail card.

Sources
  Legistar (officerecords): committees + roles, caucuses, leadership title, year first seated.
  Wikidata (SPARQL):        birth date and a Wikimedia Commons photo, where an entry exists.
Run after fetch_legistar.py (needs data/members.json with personIds and LEGISTAR_TOKEN).
"""
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests

from fetch_legistar import api_get, REPUBLICAN_CONFERENCE_BODY_ID

ROOT = Path(__file__).resolve().parent.parent
WD_UA = "NYC-CVM/1.0 (https://github.com/nbustos/NYC-CVM)"
NYC_COUNCIL_MEMBER_QID = "Q58620996"  # "member of the New York City Council"


def fold(t):
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


def name_key(name):
    """first + last name, accent-folded, without initials/suffixes."""
    toks = [t for t in re.sub(r"[.,]", " ", fold(name)).split() if t not in ("jr", "sr", "ii", "iii")]
    toks = [t for t in toks if len(t) > 1] or toks
    return (toks[0], toks[-1]) if toks else ("", "")


def wikidata_people(names):
    """Search Wikidata by name, then keep only candidates verified as NYC Council members:
    either a council position statement, or an English Wikipedia intro that mentions the
    New York City Council. This guards against namesakes (wrong photo/age is worse than none)."""
    headers = {"User-Agent": WD_UA}
    qids = set()
    for n in names:
        r = requests.get("https://www.wikidata.org/w/api.php", headers=headers, timeout=30, params={
            "action": "wbsearchentities", "search": " ".join(name_key(n)), "language": "en",
            "type": "item", "format": "json", "limit": 5})
        r.raise_for_status()
        qids.update(x["id"] for x in r.json().get("search", []))
    values = " ".join(f"wd:{q}" for q in sorted(qids))
    q = f"""SELECT ?p ?pLabel ?dob ?prec ?img ?title ?councilPos WHERE {{
      VALUES ?p {{ {values} }}
      OPTIONAL {{ ?p p:P569/psv:P569 [ wikibase:timeValue ?dob ; wikibase:timePrecision ?prec ] }}
      OPTIONAL {{ ?p wdt:P18 ?img }}
      OPTIONAL {{ ?a schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?title }}
      OPTIONAL {{ ?p p:P39/ps:P39 ?pos . ?pos rdfs:label ?pl . FILTER(LANG(?pl) = "en" && CONTAINS(?pl, "New York City Council")) BIND(true AS ?councilPos) }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
    r = requests.post("https://query.wikidata.org/sparql", data={"query": q, "format": "json"}, headers=headers, timeout=120)
    r.raise_for_status()
    people = {}
    for row in r.json()["results"]["bindings"]:
        e = people.setdefault(row["p"]["value"], {"label": row["pLabel"]["value"]})
        if "dob" in row and "birth" not in e:
            e["birth"] = {"date": row["dob"]["value"][:10], "precision": int(row["prec"]["value"])}
        for k, src in (("img", "img"), ("title", "title")):
            if src in row and k not in e:
                e[k] = row[src]["value"]
        if "councilPos" in row:
            e["verified"] = True
    # Wikipedia intro check for the rest
    titles = sorted({e["title"] for e in people.values() if "title" in e and not e.get("verified")})
    intro = {}
    for i in range(0, len(titles), 20):
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=headers, timeout=30, params={
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "redirects": 1,
            "titles": "|".join(titles[i:i + 20]), "format": "json", "exlimit": "max"})
        r.raise_for_status()
        for pg in r.json()["query"]["pages"].values():
            intro[pg.get("title")] = (pg.get("extract") or "").lower()
        for red in r.json()["query"].get("redirects", []):
            intro[red["from"]] = intro.get(red["to"], "")
    for e in people.values():
        if not e.get("verified") and "new york city council" in intro.get(e.get("title"), ""):
            e["verified"] = True
    return [e for e in people.values() if e.get("verified")]


def match_wikidata(name, people):
    key = name_key(name)
    exact = [p for p in people if name_key(p["label"]) == key]
    if len(exact) == 1:
        return exact[0]
    same_last = [p for p in people if name_key(p["label"])[1] == key[1] and name_key(p["label"])[0][:1] == key[0][:1]]
    return same_last[0] if len(same_last) == 1 else None


def commons(img_url):
    fn = img_url.rsplit("/", 1)[-1]  # URL-encoded file name
    return {"photo": f"https://commons.wikimedia.org/wiki/Special:FilePath/{fn}?width=360",
            "photoPage": f"https://commons.wikimedia.org/wiki/File:{fn}"}


def legistar_profile(person_id):
    recs = api_get("officerecords", {"$filter": f"OfficeRecordPersonId eq {person_id}"})
    today = date.today().isoformat()
    current = [r for r in recs if r["OfficeRecordEndDate"][:10] >= today and r["OfficeRecordStartDate"][:10] <= today]
    seated = sorted(r["OfficeRecordStartDate"][:4] for r in recs
                    if (r["OfficeRecordBodyName"] or "").strip() == "City Council" and r["OfficeRecordTitle"] == "Council Member")
    committees, caucuses, roles = [], [], []
    for r in current:
        body, title = (r["OfficeRecordBodyName"] or "").strip(), (r["OfficeRecordTitle"] or "").strip()
        if body == "City Council":
            if title and title.lower() != "council member":
                roles.append(title.title() if title.isupper() else title)
        elif body.startswith("Caucus - "):
            caucuses.append(body[len("Caucus - "):] + (" (Chair)" if title.upper() == "CHAIRPERSON" else ""))
        elif body.startswith("Minority (Republican) Conference"):
            caucuses.append("Republican Conference")
        elif body == "Committee of the Whole" or body.startswith("Democratic Conference"):
            continue
        elif re.match(r"(?i)(sub)?committee|task force|select committee", body):
            role = {"CHAIRPERSON": "Chair", "VICE-CHAIRPERSON": "Vice Chair"}.get(title.upper(), "")
            committees.append({"name": body, "role": role})
    committees.sort(key=lambda c: (c["role"] == "", c["name"]))
    return {"tookOffice": int(seated[0]) if seated else None, "roles": roles,
            "caucuses": sorted(set(caucuses)), "committees": committees}


def main():
    members = json.loads((ROOT / "data" / "members.json").read_text())
    try:
        people = wikidata_people([m["name"] for m in members.values()])
    except requests.RequestException as e:
        print("WARNING: Wikidata unavailable, no photos/ages this run:", e, file=sys.stderr)
        people = []
    out, missing = {}, []
    for dist, m in members.items():
        prof = {"name": m["name"], "party": m["party"]}
        if m.get("personId"):
            prof.update(legistar_profile(m["personId"]))
        wd = match_wikidata(m["name"], people)
        if wd:
            if "birth" in wd:
                prof["birth"] = wd["birth"]
            if "img" in wd:
                prof.update(commons(wd["img"]))
        if "photo" not in prof or "birth" not in prof:
            missing.append(f"D{dist} {m['name']} ({'photo ' if 'photo' not in prof else ''}{'birth' if 'birth' not in prof else ''})".replace(" )", ")"))
        out[dist] = prof
    (ROOT / "data" / "profiles.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {len(out)} profiles ({sum('photo' in p for p in out.values())} photos, "
          f"{sum('birth' in p for p in out.values())} birth dates)")
    if missing:
        print("Missing:", "; ".join(missing), file=sys.stderr)


if __name__ == "__main__":
    main()
