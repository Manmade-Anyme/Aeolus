-- TASK-002 hotfix: last-known-good NIFTY subset of Dhan's scrip master.
-- Dhan's CloudFront intermittently 403s the public CSV (observed 2026-07-06,
-- ~10 min around market open, from Fly.io); this single-row cache lets a
-- session start from the previous pull instead of dying. A day-stale copy is
-- safe: spot/VIX security_ids are static, futures ids change only on roll.

create table if not exists scrip_master_cache (
    id          integer primary key default 1 check (id = 1),
    csv_data    text not null,
    fetched_at  timestamptz not null default now()
);
