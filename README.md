# NYC Council Vote Mapper

**Live site:** https://nbustos.github.io/NYC-CVM/

An interactive map of New York City's 51 City Council districts that shows how each Council Member voted on legislation approved by the Council. It covers only bills that passed at Stated Council Meetings.

## How to use it

- **Approved / Vetoed toggle (top):** switches the whole page between bills the Council approved and bills the Mayor vetoed. For a vetoed bill that the Council later overrode, a second switch flips the map between the original Council vote and the override vote.
- **Timeline (top):** one bar for the year. Days with approved bills are highlighted green. Hover (desktop) or touch and drag (mobile) to magnify dates, then click or lift your finger to select a day. The ‹ › buttons step between meeting days.
- **Bill chips:** every approved bill from the selected day. Pick one to load its votes.
- **Map:** districts turn soft green (Yes), soft red (No) or amber (excused/abstain). Hover or tap a district to see the member, party and vote. Zoom, or jump to a borough with the buttons below the map.
- **Roll-call inspector (right/below):** a searchable list of all 51 districts with each member's vote and a link to the bill on Legistar.

The page is static: plain HTML, CSS and JavaScript with [D3](https://d3js.org/) (loaded from the jsDelivr CDN). It works on desktop and mobile.

## Where the data comes from

| File | Contents | Source |
|---|---|---|
| `data/votes.json` | Approved bills and each district's vote (`yes` / `no` / `excused`) | [NYC Council Legistar Web API](https://webapi.legistar.com/v1/nyc) (events, event items, votes) |
| `data/vetoed.json` | Bills vetoed by the Mayor: veto date, the Council vote that preceded the veto, and the override vote where there was one | Legistar API (matters with status `Vetoed`, matter histories, and `Overridden by Council` agenda items) |
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

### Setup and running locally

1. Request a free Legistar API token from the [NYC Council](https://council.nyc.gov/data/api/).
2. Add it as a repository secret named `LEGISTAR_TOKEN` (Settings → Secrets and variables → Actions).
3. To run the script locally:

   ```bash
   pip install requests
   LEGISTAR_TOKEN=your_token TARGET_YEAR=2026 python scripts/fetch_legistar.py
   ```

   To preview the site, serve the folder (for example `python -m http.server`) and open `http://localhost:8000`. Opening `index.html` directly won't load the data files.

## Known limitations

- **Party is inferred.** Legistar has no party field. Members of the Republican Conference are marked **R** and everyone else **D**. Independents and anyone registered differently must be added to `data/party_overrides.json`.
- **Borough views are approximate.** They are hardcoded by district number, and District 8 spans Manhattan and the Bronx.
- **Missing votes.** A member with no recorded vote, or one who abstained, appears as excused/abstain.
- **Vetoed list is partial by design.** It holds bills still marked vetoed plus overrides that happened in the target year, so a veto that was overridden in an earlier year won't appear.
- **Current year only.** The fetch covers one year per run (`TARGET_YEAR`) and overwrites `votes.json`, so earlier years' bills are dropped unless you change the script.
- **Unofficial.** This is an independent project, not affiliated with the NYC Council. Check votes against [Legistar](https://legistar.council.nyc.gov/) for anything official.
