CREATE SCHEMA IF NOT EXISTS monitoring;

CREATE TABLE IF NOT EXISTS monitoring.check_results (
  id            bigserial PRIMARY KEY,
  check_name    text NOT NULL,
  status        text NOT NULL CHECK (status IN ('pass', 'fail')),
  latency_ms    integer,
  error_detail  text,
  raw_response  jsonb,
  checked_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS check_results_check_name_checked_at
  ON monitoring.check_results (check_name, checked_at DESC);
