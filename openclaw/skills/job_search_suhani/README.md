# job_search_suhani

Daily retail job search for the Fountain Gate / Berwick / Cranbourne area (south-east Melbourne). Searches Seek for configured keywords and locations, deduplicates against Supabase so the same listing is never emailed twice, and sends an HTML + plain-text digest to Suhani via **Maton Gmail send** — **only when there are new matches** (unless `always_send` is enabled).

Seek already aggregates postings from most major retailers, so v1 uses Seek only. A disabled-by-default extension point in `sources/retailer_site.py` lets you add site-specific scrapers later (Kmart, Coles, etc.) without restructuring the project.

Unlike [`gmail_triage`](../gmail_triage/README.md), which only creates Gmail **drafts**, this skill **actually sends** mail to Suhani.

---

## Prerequisites

- Python 3.11+
- Supabase project with the `seen_jobs` table (see below)
- [Maton](https://maton.ai) account with an active **google-mail** connection (same setup as gmail_triage)
- Network access from the host (Raspberry Pi or local machine)

---

## Environment variables

Secrets are read from the environment only — never put them in `config.yaml`. Load via systemd (`EnvironmentFile=`) or export in the shell. If you already run gmail_triage on the Pi, you can reuse the same Maton vars from `~/.config/openclaw/openclaw.env`.

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL (`https://….supabase.co`) |
| `SUPABASE_KEY` | **Service role** key (not the anon/public key) |
| `MATON_API_KEY` | Maton API bearer token |
| `MATON_BASE_URL` | Maton gateway URL (e.g. `https://gateway.maton.ai`) |
| `GMAIL_ACCOUNT_EMAIL` | Connected Gmail address (MIME From / sending account) |

---

## Supabase table

Run this once in the Supabase **SQL Editor** (or apply from the repo root [`supabase_schema.sql`](../../../supabase_schema.sql)):

```sql
create table if not exists seen_jobs (
  job_id text primary key,
  source text not null,
  title text,
  company text,
  url text,
  first_seen timestamptz not null default now()
);
create index if not exists idx_seen_jobs_source on seen_jobs(source);
```

Use the **service role** key for `SUPABASE_KEY` so the skill can read/write without RLS policies.

---

## Configuration

Edit [`config.yaml`](config.yaml):

| Key | Purpose |
|-----|---------|
| `email.recipient` | Suhani's email address (**required** — replace `REPLACE_ME@example.com`) |
| `email.subject_prefix` | Subject line prefix |
| `search.locations` | Seek location strings (add more suburbs by editing this list) |
| `search.keywords` | Job title / keyword queries |
| `search.work_types` | Optional Seek work-type filters (`Casual/Vacation`, `Part Time`, etc.) |
| `retailer_sites` | Per-retailer career-site configs (`enabled: false` by default) |
| `always_send` | When `true`, send a daily email even when there are zero new listings |

---

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r openclaw/skills/job_search_suhani/requirements.txt
# or install all OpenClaw deps:
pip install -r requirements.txt
```

Set `email.recipient` in `config.yaml`, create the Supabase table, and export the five env vars.

---

## Run manually

From the repository root with `PYTHONPATH` set to the parent of the `openclaw` package:

```bash
export PYTHONPATH=/path/to/OpenClaw
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_KEY=your-service-role-key
export MATON_API_KEY=your-maton-api-key
export MATON_BASE_URL=https://gateway.maton.ai
export GMAIL_ACCOUNT_EMAIL=you@gmail.com

python -m openclaw.skills.job_search_suhani.job_search
```

Exit code `0` on success, `1` on configuration or runtime errors (check stderr logs).

---

## systemd timer (Raspberry Pi)

Copy the unit files from this directory to the Pi and adjust paths if your layout differs from the defaults (`/home/aeternum/src/OpenClaw`).

1. Create the env file (mode 600), or point the service at your shared OpenClaw env file if Maton vars are already there:

```bash
mkdir -p ~/.config/openclaw
cat > ~/.config/openclaw/job-search-suhani.env <<'EOF'
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
MATON_API_KEY=your-maton-api-key
MATON_BASE_URL=https://gateway.maton.ai
GMAIL_ACCOUNT_EMAIL=you@gmail.com
EOF
chmod 600 ~/.config/openclaw/job-search-suhani.env
```

Alternatively, if `~/.config/openclaw/openclaw.env` already has the Maton and Supabase vars, set `EnvironmentFile=` in the service unit to that path instead.

2. Install units (system-level example):

```bash
sudo cp job-search-suhani.service job-search-suhani.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now job-search-suhani.timer
```

3. Verify:

```bash
systemctl status job-search-suhani.timer
# Trigger a manual run:
sudo systemctl start job-search-suhani.service
journalctl -u job-search-suhani.service -n 50
```

The timer fires daily at **07:30** (`OnCalendar=*-*-* 07:30:00`, `Persistent=true`).

---

## Extending retailer career sites

Seek covers most major retailers. To scrape a retailer's own ATS directly:

1. Inspect the live HTML at the site's `search_url` (each ATS — Workday, SuccessFactors, Avature — renders differently).
2. Write an extractor function in [`sources/retailer_site.py`](sources/retailer_site.py) that returns normalized dicts (`job_id`, `source`, `title`, `company`, `location`, `url`, `posted`).
3. Register it in the `EXTRACTORS` dict keyed by the site's `name`.
4. Set `enabled: true` in `config.yaml`.

The placeholder `extract_kmart` logs "not implemented" and returns `[]`.

---

## Seek API note

This skill uses Seek's unauthenticated frontend search endpoint (`/api/jobsearch/v5/search`). It is **not** an officially documented API and the response shape may change without notice. If searches suddenly return zero results, check Seek's network tab in a browser and update `sources/seek.py` if needed.

---

## Architecture

```
config.yaml → job_search.py
                ├── sources/seek.py        (keyword × location searches)
                ├── sources/retailer_site.py (optional, disabled in v1)
                ├── dedup.py               (Supabase seen_jobs)
                └── emailer.py             (Maton Gmail send)
```

No company-name filtering — every keyword/location match is a candidate.
