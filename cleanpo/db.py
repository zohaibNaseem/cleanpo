"""Local DuckDB database: master lists, processed orders, issues and settings."""

from __future__ import annotations

from pathlib import Path

import duckdb

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS order_id_seq START 1;

CREATE TABLE IF NOT EXISTS orders (
    id               INTEGER PRIMARY KEY DEFAULT nextval('order_id_seq'),
    file_name        VARCHAR,
    stored_path      VARCHAR,
    source_type      VARCHAR,
    received_at      TIMESTAMP DEFAULT current_timestamp,
    customer_account VARCHAR,
    customer_name    VARCHAR,
    sender_email     VARCHAR,
    po_number        VARCHAR,
    order_date       VARCHAR,
    delivery_date    VARCHAR,
    status           VARCHAR,      -- ready | needs_review | approved | rejected | not_order | exported
    had_problems     BOOLEAN DEFAULT FALSE,
    read_by          VARCHAR,      -- claude | replay
    model            VARCHAR,
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    api_cost_usd     DOUBLE DEFAULT 0,
    n_lines          INTEGER DEFAULT 0,
    total            DOUBLE DEFAULT 0,
    review_seconds   DOUBLE DEFAULT 0,
    reviewed_at      TIMESTAMP,
    exported_at      TIMESTAMP,
    is_demo          BOOLEAN DEFAULT FALSE,
    notes            VARCHAR
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_id           INTEGER,
    line_no            INTEGER,
    raw_text           VARCHAR,
    description        VARCHAR,
    item_code_written  VARCHAR,
    qty_written        DOUBLE,
    unit_written       VARCHAR,
    unit_price_written DOUBLE,
    qty                DOUBLE,
    unit               VARCHAR,
    product_code       VARCHAR,
    product_name       VARCHAR,
    match_method       VARCHAR,    -- code | customer_code | memory | name | ai | human
    match_score        DOUBLE,
    candidates         VARCHAR,    -- JSON list of [code, name, score]
    price              DOUBLE,
    amount             DOUBLE,
    removed            BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS issues (
    order_id   INTEGER,
    line_no    INTEGER,            -- NULL for an issue on the whole order
    code       VARCHAR,
    severity   VARCHAR,            -- block | info
    message    VARCHAR,
    value_usd  DOUBLE DEFAULT 0,   -- money at risk if nobody had caught it
    resolved   BOOLEAN DEFAULT FALSE,
    resolution VARCHAR,
    is_demo    BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS learned_aliases (
    customer_account VARCHAR,
    written_as       VARCHAR,
    product_code     VARCHAR,
    learned_at       TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS settings (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR
);
"""

MASTER_FILES = {
    "products": "products.csv",
    "customers": "customers.csv",
    "prices": "prices.csv",
    "aliases": "aliases.csv",
    "customer_item_codes": "customer_item_codes.csv",
    "order_history": "order_history.csv",
}

DEFAULT_SETTINGS = {
    "baseline_minutes_per_order": "6",
    "baseline_source": "Default value. Replace with the time measured during the trial.",
    "hourly_rate_usd": "22.20",
    "hourly_rate_source": "BLS OEWS May 2025 median wage, order clerks (43-4151)",
    "model": "claude-opus-5-5",
    "company_name": "Lone Pine Food Supply",
}


def connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        con.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT DO NOTHING", [key, value])
    return con


def load_master(con: duckdb.DuckDBPyConnection, folder: str | Path) -> dict[str, int]:
    """Replace the master tables with the CSV files in the folder. Return the row counts."""
    folder = Path(folder)
    counts = {}
    for table, file_name in MASTER_FILES.items():
        path = folder / file_name
        if not path.exists():
            if table in ("products", "customers", "prices"):
                raise FileNotFoundError(f"Required master file is missing: {path}")
            continue
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS "
            f"SELECT * FROM read_csv(?, header=true, all_varchar=true)",
            [str(path)],
        )
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def get_setting(con: duckdb.DuckDBPyConnection, key: str) -> str:
    row = con.execute("SELECT value FROM settings WHERE key = ?", [key]).fetchone()
    return row[0] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0])
