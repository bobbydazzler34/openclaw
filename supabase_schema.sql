-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Table: scanned emails
create table if not exists gmail_triage_scans (
  id              uuid primary key default uuid_generate_v4(),
  email_id        text not null unique,          -- Gmail message ID
  account         text not null,                 -- Gmail address
  subject         text,
  sender          text,
  received_at     timestamptz,
  scanned_at      timestamptz default now(),
  classification  text check (classification in ('important', 'deletable', 'neutral')),
  draft_created   boolean default false,
  flagged_delete  boolean default false,
  run_id          uuid not null                  -- links back to a specific skill run
);

-- Table: skill run log
create table if not exists gmail_triage_runs (
  id              uuid primary key default uuid_generate_v4(),
  account         text not null,
  started_at      timestamptz default now(),
  completed_at    timestamptz,
  emails_scanned  int default 0,
  drafts_created  int default 0,
  flagged_delete  int default 0,
  status          text check (status in ('running', 'success', 'failed')),
  error_message   text
);

-- Row-level security (enable but allow service role full access)
alter table gmail_triage_scans enable row level security;
alter table gmail_triage_runs enable row level security;

-- Policy: only service role can read/write (anon/authenticated blocked)
create policy "service_role_only_scans"
  on gmail_triage_scans
  using (auth.role() = 'service_role');

create policy "service_role_only_runs"
  on gmail_triage_runs
  using (auth.role() = 'service_role');

-- Index for fast deduplication lookups
create index if not exists idx_scans_email_id on gmail_triage_scans(email_id);
create index if not exists idx_scans_account on gmail_triage_scans(account);
create index if not exists idx_runs_account on gmail_triage_runs(account);
