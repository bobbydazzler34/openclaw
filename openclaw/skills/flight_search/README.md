# flight_search

Daily flight search for configured routes: **SearchAPI.io** calendar prices plus **SerpApi** detailed itineraries, posted to Discord as embeds via the Sempiternal bot token.

Runs on the Raspberry Pi **sempiternal** via a systemd timer once per day.

---

## Prerequisites

- Python 3.11+
- SearchAPI.io account (`google_flights_calendar`) — free tier: **100 searches/month**
- SerpApi account (`google_flights`) — free tier: **250 searches/month**
- Discord bot token (`DISCORD_BOT_TOKEN`, shared with other OpenClaw skills)
- Dedicated Discord channel ID for flight results

---

## Environment variables

Secrets are read from the environment only — never put them in `config.yaml`. systemd loads `/etc/openclaw/secrets.env` before the process starts.

| Variable | Purpose |
|----------|---------|
| `SEARCHAPI_KEY` | SearchAPI.io API key (calendar searches) |
| `SERPAPI_KEY` | SerpApi API key (detailed flight searches) |
| `DISCORD_BOT_TOKEN` | Sempiternal bot token (shared) |
| `DISCORD_FLIGHT_SEARCH_CHANNEL_ID` | Target Discord channel snowflake ID |

Access secrets in Python via `from openclaw.secrets import get_secret`.

### Add to `/etc/openclaw/secrets.env`

```
SEARCHAPI_KEY=your_searchapi_key_here
SERPAPI_KEY=your_serpapi_key_here
DISCORD_FLIGHT_SEARCH_CHANNEL_ID=your_channel_snowflake_here
```

`DISCORD_BOT_TOKEN` should already be present if `revenuecat_metrics` is deployed. If not:

```
DISCORD_BOT_TOKEN=your_sempiternal_bot_token_here
```

File permissions: `640`, owner `root:openclaw`. No `export` keyword on lines.

---

## Configuration

Edit [`config.yaml`](config.yaml):

| Key | Purpose |
|-----|---------|
| `search_mode` | Default mode: `calendar_then_flights` or `flights_only` |
| `http_timeout_seconds` | Per-request HTTP timeout (default `30`) |
| `default_top_n` | Max detailed flights per route (default `5`) |
| `deep_search` | SerpApi `deep_search` flag for detail step |
| `gl`, `hl`, `currency` | Locale and currency (default `au`, `en`, `AUD`) |
| `calendar.window_days` | Date window centered on anchor (default `14`) |
| `calendar.top_dates` | Calendar lines posted to Discord (default `10`) |
| `routes` | List of routes to search each run |

### Route format

Each route is an **independent** search.

```yaml
search_mode: calendar_then_flights

calendar:
  window_days: 14
  top_dates: 10

routes:
  - name: melbourne_to_san_francisco
    origin: MEL
    destination: SFO
    trip_type: one_way
    outbound_date_rule:
      type: days_out
      days: 14
    adults: 1
    children: 0
    top_n: 5
```

Per-route overrides: `search_mode`, `calendar_window_days`, `calendar_top_dates`.

**`flights_only` mode** — skip calendar, SerpApi search on anchor date only (fallback when SearchAPI quota is exhausted).

### Multi-city routes

Set `trip_type: multi_city` with an ordered `legs` list (minimum 2). Top-level `origin` / `destination` are not required.

```yaml
  - name: mel_lhr_cdg_loop
    trip_type: multi_city
    legs:
      - origin: MEL
        destination: LHR
        date_rule: { type: days_out, days: 30 }
      - origin: LHR
        destination: CDG
        date_rule: { type: days_out, days: 37 }
      - origin: CDG
        destination: MEL
        date_rule: { type: days_out, days: 44 }
    adults: 4
    children: 1
    top_n: 5
```

Each leg supports fixed `date: YYYY-MM-DD` or `date_rule: { type: days_out, days: N }`. Optional per-leg `times` (SerpApi 4-integer string) is passed through to `multi_city_json`.

SerpApi uses `type=3` with `multi_city_json` for the detail search. Discord flights embed title shows the full airport sequence (e.g. `MEL → LHR → CDG → MEL — 2026-08-01 · 2026-08-08 · 2026-08-15`).

**Calendar for multi-city:** SearchAPI has no native multi-city calendar. In `calendar_then_flights` mode, the skill runs a **one-way calendar on leg 1 only**, posts a green calendar embed, then shifts all leg dates by the same delta when picking the cheapest leg-1 date before the SerpApi multi-city detail search.

---

## How each run works

For `calendar_then_flights` (default):

1. **SearchAPI** `google_flights_calendar` — price grid over a ±7 day window around the anchor date
2. Post **calendar embed** (green) with cheapest dates
3. **SerpApi** `google_flights` — detailed top-N flights on the **cheapest calendar date**
4. Post **flights embed** (blue) with rich itinerary details:
   - Clickable title → Google Flights search (`search_metadata.google_flights_url`)
   - Per-itinerary blocks: price, airlines, stops, duration, departure/arrival times, flight numbers, layovers, carbon emissions
   - Footer with typical price range and price level from `price_insights`
   - Airline logo thumbnail on the cheapest result

### Fallback behavior

| Situation | Behavior |
|-----------|----------|
| SearchAPI quota exhausted | Skip calendar; SerpApi search on anchor date |
| SerpApi quota exhausted | Post calendar only (if available) |
| Calendar returns empty | SerpApi search on anchor date |
| Route `search_mode: flights_only` | SerpApi only (1 call/route) |
| Multi-city route | SerpApi `type=3`; calendar uses leg-1 one-way proxy + date shift |

---

## Quota

Tracked separately in `quota_state.json` (gitignored):

| Provider | Monthly limit | Warning at |
|----------|---------------|------------|
| SearchAPI | 100 | 80 |
| SerpApi | 250 | 200 |

**2 routes/day in calendar mode:** ~2 SearchAPI + ~2 SerpApi = **~60 + ~60/month** — within both free tiers.

When a provider reaches its warning threshold, a one-time Discord message is posted for that calendar month.

---

## Run manually

```bash
export PYTHONPATH=/path/to/OpenClaw
# export SEARCHAPI_KEY=... SERPAPI_KEY=... DISCORD_BOT_TOKEN=... DISCORD_FLIGHT_SEARCH_CHANNEL_ID=...

python -m openclaw.skills.flight_search.flight_search --once
python -m openclaw.skills.flight_search.flight_search --dry-run
```

---

## systemd deployment

```bash
sudo cp openclaw/skills/flight_search/flight-search.service \
        openclaw/skills/flight_search/flight-search.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now flight-search.timer
```

Default schedule: daily at **08:00**.

```bash
journalctl -u flight-search.service -f
sudo systemctl start flight-search.service
```

---

## Architecture

```
flight-search.timer (daily 08:00)
  └── flight-search.service (oneshot)
        └── flight_search.py
              ├── quota.py                      → dual JSON counters
              ├── searchapi_calendar_client.py  → SearchAPI calendar
              ├── serpapi_client.py             → SerpApi detail flights
              └── discord_notifier.py           → calendar + flights embeds
```

---

## Security notes

- Do not log `SEARCHAPI_KEY`, `SERPAPI_KEY`, or `DISCORD_BOT_TOKEN`.
- `/etc/openclaw/secrets.env` should be mode `640`, owned `root:openclaw`.
