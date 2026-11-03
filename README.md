# NY Vote Mapper

**Live site:** https://nbustos.github.io/NY-Vote-Mapper/

An interactive map of New York City's 51 City Council districts that shows how each Council Member voted on legislation approved by the Council. It covers only bills that passed at Stated Council Meetings.

## Two views: NYC Council and NY State Senate

A switch at the top of every page toggles between the **NYC Council** map (`index.html`) and the **NY State Legislature** map (`state.html`). The state page has the same features: calendar timeline, Passed | Vetoed toggle, district map, roll-call inspector and member profile cards. See [NY State Senate view](#ny-state-senate-view) for its data sources and limits.

## How to use it

- **Approved / Vetoed toggle (top):** switches the whole page between bills the Council approved and bills the Mayor vetoed. For a vetoed bill that the Council later overrode, a second switch flips the map between the original Council vote and the override vote.
- **Timeline (top):** one bar for the year. Days with approved bills are highlighted green. Hover (desktop) or touch and drag (mobile) to magnify dates, then click or lift your finger to select a day. The ‹ › buttons step between meeting days.
- **Bill chips:** every approved bill from the selected day. Pick one to load its votes.
- **Map:** districts turn soft green (Yes), soft red (No) or amber (excused/abstain). Hover or tap a district to see the member, party and vote. Zoom, or jump to a borough with the buttons below the map.
- **Member profile card:** click a district on the map, or a row in the roster, to open that Council Member's card: photo, age, year they took office, party, leadership role, caucuses, committees (with chair roles) and, if a bill is selected, how they voted on it.
- **Roll-call inspector (right/below):** a searchable list of all 51 districts with each member's vote and a link to the bill on Legistar.

- **How a bill becomes law (link, top right):** a separate illustrated page, `how-a-bill-becomes-law.html`, walking through the path from idea to Local Law, including the 26-vote and 34-vote thresholds.

The page is static: plain HTML, CSS and JavaScript with [D3](https://d3js.org/) (loaded from the jsDelivr CDN). It works on desktop and mobile.

## Where the data comes from

| File | Contents | Source |
|---|---|---|
| `data/votes.json` | Approved bills and each district's vote (`yes` / `no` / `excused`) | [NYC Council Legistar Web API](https://webapi.legistar.com/v1/nyc) (events, event items, votes) |
| `data/vetoed.json` | Bills vetoed by the Mayor: veto date, the Council vote that preceded the veto, and the override vote where there was one | Legistar API (matters with status `Vetoed`, matter histories, and `Overridden by Council` agenda items) |
| `data/profiles.json` | Per-member profile: committees and roles, caucuses, leadership title, year first seated, plus birth date and photo where available | Committees, caucuses and start year from Legistar `officerecords`; birth date and photo from [Wikidata](https://www.wikidata.org/) and [Wikimedia Commons](https://commons.wikimedia.org/), matched by name and verified against the person's English Wikipedia intro |
| `data/members.json` | District → member name and party | Legistar vote records; party from Legistar's Minority (Republican) Conference membership |
| `data/districts.geojson` | District boundaries | [NYC Open Data: City Council Districts](https://data.cityofnewyork.us/d/872g-cjhh) (shoreline-clipped, coordinates rounded to shrink the file) |
| `data/district_overrides.json` | Manual PersonId → district fixes | Maintained by hand, verified against [council.nyc.gov](https://council.nyc.gov/) member pages |
| `data/party_overrides.json` | Manual district → party fixes | Maintained by hand |

Links to each bill use Legistar's `gateway.aspx?M=L&ID=<MatterId>` redirect, because the API's matter IDs differ from the website's internal IDs.

### How the vote data is built

`scripts/fetch_legistar.py` runs against the Legistar API:

1. Fetches City Council events (body ID 1) for the target year (`TARGET_YEAR`, currently 2026).
2. For each event, keeps agenda items that passed (`EventItemPassedFlag == 1`) and are legislation (`Int` or `Res` files). The roll-call flag is not used, because approved items carry 0 in it. Items without vote records are skipped.
3. Fetches each item's individual votes and maps them to districts 1–51. A member's district is read from the `council.nyc.gov/district-N/` link on their Legistar person record. Members whose link is a name slug are covered by `district_overrides.json`, and a last-name table is the final fallback.
4. Builds the vetoed list: matters whose status is `Vetoed`, plus agenda items the Council overrode in the target year. The calendar date is the Mayor's veto date (from the matter's history). The votes are the Council's last approval vote before the veto, and, if overridden, the override vote.
5. Writes `votes.json`, `vetoed.json` and `members.json`. Former members are included when mapping districts, so older votes resolve correctly.

A GitHub Actions workflow (`.github/workflows/update-votes.yml`) runs this every day at 06:00 UTC, or on demand from the Actions tab, and commits any changes. GitHub Pages then serves the updated data.

`scripts/fetch_profiles.py` runs after it (with `continue-on-error`, so a Wikidata hiccup never blocks the vote update) and builds `profiles.json`. Photos are hotlinked from Wikimedia Commons, which publishes them under free licenses; the card links to each file's page for credit and license.

### Setup and running locally

1. Request a free Legistar API token from the [NYC Council](https://council.nyc.gov/data/api/).
2. Add it as a repository secret named `LEGISTAR_TOKEN` (Settings → Secrets and variables → Actions).
3. To run the script locally:

   ```bash
   pip install requests
   LEGISTAR_TOKEN=your_token TARGET_YEAR=2026 python scripts/fetch_legistar.py
   ```

   To preview the site, serve the folder (for example `python -m http.server`) and open `http://localhost:8000`. Opening `index.html` directly won't load the data files.

## NY State Senate view

`state.html` shows how each of the 63 State Senators voted on bills the Senate passed in the current two-year session, with the same controls as the NYC page. "Signed" lists the bills the Governor signed into law, dated by the signing date (the card also shows the Senate vote date). "Vetoed" lists bills the Governor vetoed (the date shown is the veto date; the votes are the Senate's last floor vote on the bill).

| File | Contents | Source |
|---|---|---|
| `data/state/votes.json` | Bills passed on a Senate floor roll call (latest floor vote per bill) | [NY Senate Open Legislation API](https://legislation.nysenate.gov/) (needs a free key, repo secret `NYS_LEGISLATION_KEY`) |
| `data/state/vetoed.json` | Bills with a Governor's veto | Same API (bill milestones) |
| `data/state/members.json`, `profiles.json` | Senator per district, committees (with chairs), first session served | Same API (members, committees); party and leadership roles from Wikipedia's *New York State Senate* article; birth date and photo from Wikidata and Wikimedia Commons, with the same verified-match rule as NYC |
| `data/state/districts.geojson` | Senate district boundaries | US Census Bureau TIGERweb, 2026 State Legislative Districts (Upper), simplified |
| `data/state/regions.json` | District to region (NYC, Long Island, Hudson Valley & Capital, Central, Western) | Derived from the boundaries; approximate |

`scripts/fetch_nys.py` builds these nightly in the same workflow. Limits:

- **Senate only.** The API has no Assembly roll calls, so the Assembly (150 districts) is not covered.
- **"Took office"** is the first session year the API lists for the member, so it can be earlier than the year they were elected to their current seat.
- **Excused** includes absent and abstaining senators. A vacant seat (currently District 12) has no senator listed.
- **Region labels** are approximate; New York City is shown as one region.
- The state page links to its own illustrated explainer, `how-a-bill-becomes-law-ny-state.html` (Senate, Assembly, Governor, vote thresholds and the Senate committees). The NYC page links to the NYC version.

## Known limitations

- **Party is inferred.** Legistar has no party field. Members of the Republican Conference are marked **R** and everyone else **D**. Independents and anyone registered differently must be added to `data/party_overrides.json`.
- **Profile gaps.** Photos and ages come from Wikidata, which is missing entries for many newer members, and a candidate is accepted only if their Wikipedia intro mentions the NYC Council. Missing fields show "Not available". "Took office" is the year of the member's earliest Council Member record in Legistar, which is not always the election year.
- **Borough views are approximate.** They are hardcoded by district number, and District 8 spans Manhattan and the Bronx.
- **Missing votes.** A member with no recorded vote, or one who abstained, appears as excused/abstain.
- **Vetoed list is partial by design.** It holds bills still marked vetoed plus overrides that happened in the target year, so a veto that was overridden in an earlier year won't appear.
- **Current year only.** The fetch covers one year per run (`TARGET_YEAR`) and overwrites `votes.json`, so earlier years' bills are dropped unless you change the script.
- **Unofficial.** This is an independent project, not affiliated with the NYC Council. Check votes against [Legistar](https://legistar.council.nyc.gov/) for anything official.
