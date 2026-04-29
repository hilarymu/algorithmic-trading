# ADR-010 — SQLite as Persistent Data Store (Replacing JSON Files)

**Status:** Deferred  
**Date:** 2026-04-25  
**Deferred:** 2026-04-26 — revisit when ≥ 50 closed positions exist; analytics benefit is not concrete until then. JSON I/O is not the bottleneck at current scale.  
**Deciders:** Project team  
**Target phase:** Phase 4

---

## Context

All pipeline state is currently stored in flat JSON files on disk:

| File | Current size | Growth rate |
|---|---|---|
| `iv_history.json` | ~2 MB (79k entries, 512 symbols × 157 days) | +~15 KB/day (512 symbols × 1 reading each) |
| `positions_state.json` | < 1 KB (0 trades yet) | +~200 bytes per closed trade |
| `options_picks_history.json` | < 1 KB (5 picks) | +~100 bytes per screener pick |
| `iv_rank_cache.json` | ~80 KB | Fully rewritten daily |
| `options_signal_quality.json` | ~5 KB | Fully rewritten daily |
| `options_improvement_report.json` | ~3 KB | Fully rewritten daily |

### What is working fine

At current scale, JSON I/O is not the bottleneck. A full pipeline run takes ~6–45 seconds,
and essentially all of that time is Alpaca API network calls (~512 HTTP requests). JSON
parsing of the largest file (`iv_history.json` at ~2 MB) takes < 50ms in Python.

### What is not working well

1. **No query capability.** To answer "what's the win rate for IV rank 70–100 in bull regime?"
   you must load the entire `positions_state.json` into Python, iterate, and filter manually.
   Every such query is a full table scan in application code.

2. **No transactional safety.** A crash mid-write leaves a corrupt or half-written JSON file.
   `positions_state.json` is especially dangerous — it's the live trading ledger and has no
   recovery mechanism beyond a manual backup.

3. **Growth trajectory.** `iv_history.json` grows ~5 MB/year. At 5 years: 25 MB. Still fine
   for a single JSON file, but parsing a 25 MB file on every run is unnecessary work.
   `positions_state.json` and `picks_history.json` grow indefinitely and will eventually
   make full-file reads slow.

4. **No indexing.** Reading the IV history for a single symbol requires loading all 512
   symbols. A database can fetch a single symbol's 252-day window in microseconds with an
   index on `(symbol, date)`.

5. **No concurrent read safety.** Multiple processes (daily run + intraday monitor) read
   and write `positions_state.json` without locking. Currently safe only because Task
   Scheduler runs them in separate time windows. A future move to more frequent polling
   would create race conditions.

6. **Dashboard / analytics limitations.** Building a proper dashboard means querying
   historical outcomes, IV rank trends, strategy performance by regime, etc. SQL makes
   these queries trivial; JSON makes them painful.

---

## Decision

**Migrate JSON data storage to SQLite.**

SQLite is the correct choice for this system at its scale and deployment model:
- Embedded — no separate server process, no network, no configuration
- Single file (`options_data.db`) — same simplicity as JSON, better than a fleet of files
- ACID transactions — write-ahead logging prevents corrupt state on crash
- Python stdlib — `import sqlite3`, zero extra dependencies
- Fast for this scale — millions of rows handled comfortably; our dataset is small
- SQL query capability — enables analytics without loading everything into memory
- File-portable — copy `options_data.db` to move the entire data store

### Tables

```sql
-- IV history: one row per symbol per date
CREATE TABLE iv_history (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,          -- ISO-8601 'YYYY-MM-DD'
    iv          REAL NOT NULL,
    is_proxy    INTEGER NOT NULL DEFAULT 1,  -- 1 = HV30 proxy, 0 = real snapshot
    PRIMARY KEY (symbol, date)
);
CREATE INDEX idx_iv_history_symbol ON iv_history (symbol, date DESC);

-- IV rank cache: one row per symbol (latest computed rank)
CREATE TABLE iv_rank_cache (
    symbol          TEXT PRIMARY KEY,
    iv_rank         REAL,
    iv_percentile   REAL,
    current_iv      REAL,
    min_iv_252d     REAL,
    max_iv_252d     REAL,
    updated_date    TEXT
);

-- All positions (open and closed)
CREATE TABLE positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    contract_symbol     TEXT NOT NULL,
    entry_date          TEXT NOT NULL,
    entry_premium       REAL,
    entry_price         REAL,
    strike              REAL,
    expiry              TEXT,
    dte_at_entry        INTEGER,
    contracts           INTEGER DEFAULT 1,
    iv_rank_at_entry    REAL,
    rsi_at_entry        REAL,
    regime              TEXT,
    alpaca_order_id     TEXT,
    -- exit fields (NULL while open)
    exit_date           TEXT,
    exit_premium        REAL,
    hold_days           INTEGER,
    pnl_pct             REAL,
    exit_reason         TEXT,
    status              TEXT DEFAULT 'open'   -- 'open' | 'closed'
);
CREATE INDEX idx_positions_status ON positions (status, entry_date);
CREATE INDEX idx_positions_symbol ON positions (symbol);

-- Screener picks history
CREATE TABLE picks_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    price           REAL,
    rsi             REAL,
    iv_rank         REAL,
    volume_ratio    REAL,
    strategy        TEXT,
    signal_score    REAL,
    regime          TEXT,
    near_earnings   INTEGER DEFAULT 0
);
CREATE INDEX idx_picks_date ON picks_history (date DESC);

-- Optimizer applied changes (audit trail)
CREATE TABLE optimizer_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    applied_at  TEXT NOT NULL,
    param       TEXT NOT NULL,
    from_value  TEXT,
    to_value    TEXT,
    reason      TEXT,
    confidence  TEXT,
    n_closed    INTEGER
);
```

### Migration approach

1. Write a one-time `migrate_json_to_sqlite.py` script that reads all existing JSON files
   and inserts their data into the SQLite tables.
2. Update each module to read/write SQLite instead of JSON:
   - `iv_tracker.py` and `iv_backfill.py` → `iv_history` and `iv_rank_cache` tables
   - `options_monitor.py` and `options_executor.py` → `positions` table
   - `options_screener.py` → `picks_history` table
   - `options_optimizer.py` → `optimizer_changes` table
3. Keep `options_candidates.json` and `options_pending_entries.json` as JSON — they are
   transient inter-process handoffs within a single pipeline run, not persistent state.
   No benefit from putting them in a database.
4. Keep `options_config.json` as JSON — it is a human-edited config file, not data storage.
5. Keep `alpaca_config.json` as JSON — credentials file, never queried.

### Sample queries that become trivial

```sql
-- Win rate by IV rank bucket
SELECT
    CASE
        WHEN iv_rank_at_entry < 40  THEN '<40'
        WHEN iv_rank_at_entry < 55  THEN '40-55'
        WHEN iv_rank_at_entry < 70  THEN '55-70'
        WHEN iv_rank_at_entry < 85  THEN '70-85'
        ELSE '85-100'
    END AS iv_bucket,
    COUNT(*) AS n,
    ROUND(AVG(CASE WHEN pnl_pct > 0 THEN 100.0 ELSE 0 END), 1) AS win_rate,
    ROUND(AVG(pnl_pct), 2) AS avg_pnl,
    ROUND(AVG(hold_days), 1) AS avg_hold
FROM positions
WHERE status = 'closed'
GROUP BY iv_bucket;

-- IV rank trend for a symbol
SELECT date, iv, iv_rank
FROM iv_history h
JOIN iv_rank_cache c USING (symbol)
WHERE symbol = 'TSCO'
ORDER BY date DESC
LIMIT 30;

-- Performance by regime
SELECT regime, COUNT(*), AVG(pnl_pct), SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
FROM positions WHERE status = 'closed'
GROUP BY regime;
```

---

## Alternatives considered

### Option A — Keep JSON files (status quo)
- Pros: zero migration effort, zero new dependencies, simplest possible
- Cons: no query capability, no transactional safety, full-file reads grow over time
- Rejected for Phase 4: the analytics use case alone justifies the migration

### Option B — PostgreSQL or MySQL
- Pros: full-featured RDBMS, good tooling, scales to very large datasets
- Cons: requires a separate server process, connection management, credentials, backup setup.
  Massively over-engineered for a single-machine pipeline with < 1 GB of data.
- Rejected: the operational overhead is not justified at this scale

### Option C — MongoDB (or other document store)
- Pros: document model matches existing JSON structure closely; flexible schema
- Cons: extra dependency (`pymongo`), separate server process, no joins or relational queries.
  The data IS relational (positions join iv_history by symbol and date). A document store
  actually makes the analytics queries harder, not easier.
- Rejected: relational queries are the main motivation; a document store doesn't solve them

### Option D — DuckDB (analytical columnar database)
- Pros: excellent for time-series analytics, columnar storage ideal for IV history,
  can query Parquet/CSV files directly, excellent Python integration
- Cons: extra dependency, optimised for read-heavy analytical workloads (not OLTP).
  The pipeline does many small writes (one position update per exit check) which are not
  DuckDB's strength. Overkill for this scale.
- Interesting: DuckDB would be the right choice if the system grew to millions of rows
  and needed fast aggregate queries across years of IV history. Flag for re-evaluation if
  `iv_history` exceeds 5 million rows.
- Rejected for now: SQLite handles both the OLTP (position writes) and OLAP (analytics)
  workloads at this scale

### Option E — Redis (in-memory cache)
- Useful only for `iv_rank_cache` — fast symbol→rank lookups with TTL. But the cache is
  already fast as JSON (512 symbols, < 1ms). Adds a persistent service dependency.
- Rejected: adds infrastructure without meaningful performance gain at this scale

---

## Consequences

**Positive:**
- SQL queries enable all analytics the optimizer and dashboard need
- ACID transactions protect `positions_state` from partial-write corruption
- Index on `(symbol, date)` makes single-symbol IV history reads ~1000× faster than full-file load
- `iv_history` inserts are appends, not full rewrites — faster for 512 symbols/day
- Concurrency safe: SQLite's WAL mode allows concurrent readers + one writer
- Single `options_data.db` file is easier to back up than 9 separate JSON files

**Negative / tradeoffs:**
- Migration effort: all 6 modules need updates; one-time migration script needed
- Loses human-readability of JSON — `cat iv_history.json` no longer works;
  need `sqlite3 options_data.db "SELECT * FROM iv_history WHERE symbol='TSCO' ORDER BY date DESC LIMIT 5"`
- SQLite's WAL mode still has limits: high write concurrency (> ~10 concurrent writers)
  is not supported. Not a concern for this pipeline.

**Not changed:**
- `options_candidates.json` and `options_pending_entries.json` remain JSON (transient)
- `options_config.json` remains JSON (human-edited config)
- `alpaca_config.json` remains JSON (credentials)
- Pipeline architecture and all module interfaces unchanged

---

## Implementation notes

When implementing, use `sqlite3` from the Python standard library.
A shared `options_loop/db.py` module should:
- Own the connection and schema creation (`CREATE TABLE IF NOT EXISTS`)
- Provide typed helper functions (`insert_position()`, `update_position_exit()`,
  `fetch_iv_history(symbol, days=252)`, etc.)
- Use parameterized queries everywhere (prevent injection, improve performance)
- Use `isolation_level=None` (autocommit) for cache writes; explicit transactions for
  position state changes

---

## Links

- [reference/data-formats.md](../../reference/data-formats.md) — current JSON schemas (source for table design)
- [architecture/08-crosscutting-concepts.md](../08-crosscutting-concepts.md) — error handling patterns
- Python docs: [sqlite3 — DB-API 2.0 interface](https://docs.python.org/3/library/sqlite3.html)
