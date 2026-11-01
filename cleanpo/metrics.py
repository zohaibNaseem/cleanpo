"""Dashboard numbers. Every number is counted from the orders table; nothing is estimated
except the manual-entry baseline, which the client measures during the trial."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Optional

import duckdb
import pandas as pd

from . import db

def _where(start: Optional[datetime], end: Optional[datetime], account: Optional[str], demo: bool,
           alias: str = "") -> tuple[str, list]:
    a = f"{alias}." if alias else ""
    clauses, params = [f"{a}status <> 'not_order'"], []
    if start:
        clauses.append(f"{a}received_at >= ?")
        params.append(start)
    if end:
        clauses.append(f"{a}received_at < ?")
        params.append(end)
    if account:
        clauses.append(f"{a}customer_account = ?")
        params.append(account)
    if not demo:
        clauses.append(f"NOT {a}is_demo")
    return " AND ".join(clauses), params


def kpis(con: duckdb.DuckDBPyConnection, start=None, end=None, account=None, include_demo=True) -> dict:
    baseline = float(db.get_setting(con, "baseline_minutes_per_order"))
    rate = float(db.get_setting(con, "hourly_rate_usd"))
    where, params = _where(start, end, account, include_demo)
    row = con.execute(
        f"""SELECT count(*), coalesce(sum(n_lines), 0),
                   count(*) FILTER (WHERE NOT had_problems AND status IN ('ready', 'exported')),
                   count(*) FILTER (WHERE status = 'needs_review'),
                   coalesce(sum(review_seconds), 0) / 60.0,
                   coalesce(sum(api_cost_usd), 0),
                   count(*) FILTER (WHERE read_by = 'replay'),
                   coalesce(sum(total) FILTER (WHERE status IN ('ready', 'approved', 'exported')), 0)
            FROM orders WHERE {where}""", params).fetchone()
    orders, lines, automatic, waiting, review_min, api_cost, replayed, value = row
    handled = orders - waiting
    saved_min = max(0.0, handled * baseline - review_min)
    not_orders = con.execute(
        f"SELECT count(*) FROM orders WHERE status = 'not_order'"
        + ("" if include_demo else " AND NOT is_demo")).fetchone()[0]
    return {
        "orders": orders, "lines": int(lines), "automatic": automatic, "waiting": waiting,
        "automatic_pct": (automatic / orders * 100) if orders else 0.0,
        "review_minutes": review_min, "hours_saved": saved_min / 60, "money_saved": saved_min / 60 * rate,
        "api_cost": api_cost, "replayed": replayed, "order_value": value, "not_orders": not_orders,
        "baseline_minutes": baseline, "hourly_rate": rate,
    }


def errors_caught(con, start=None, end=None, account=None, include_demo=True) -> pd.DataFrame:
    where, params = _where(start, end, account, include_demo, alias="o")
    return con.execute(
        f"""SELECT i.code, count(*) AS n, round(sum(i.value_usd), 2) AS value_usd
            FROM issues i JOIN orders o ON o.id = i.order_id
            WHERE i.severity = 'block' AND i.code NOT IN ('READ_FAILED')
              AND {where}
            GROUP BY i.code ORDER BY n DESC""", params).df()


def weekly(con, account=None, include_demo=True, weeks=8) -> pd.DataFrame:
    baseline = float(db.get_setting(con, "baseline_minutes_per_order"))
    where, params = _where(None, None, account, include_demo)
    frame = con.execute(
        f"""SELECT date_trunc('week', received_at) AS week, count(*) AS orders,
                   count(*) FILTER (WHERE NOT had_problems AND status IN ('ready', 'exported')) AS automatic,
                   count(*) FILTER (WHERE status = 'needs_review') AS waiting,
                   coalesce(sum(review_seconds), 0) / 60.0 AS review_min
            FROM orders WHERE {where} GROUP BY 1 ORDER BY 1""", params).df()
    if frame.empty:
        return frame
    frame["hours_saved"] = ((frame["orders"] - frame["waiting"]) * baseline - frame["review_min"]).clip(lower=0) / 60
    frame["automatic_pct"] = frame["automatic"] / frame["orders"] * 100
    return frame.tail(weeks)


def by_customer(con, start=None, end=None, include_demo=True) -> pd.DataFrame:
    where, params = _where(start, end, None, include_demo)
    return con.execute(
        f"""SELECT coalesce(customer_name, '(unknown)') AS customer, count(*) AS orders,
                   count(*) FILTER (WHERE had_problems) AS with_problems,
                   round(coalesce(sum(total) FILTER (WHERE status IN ('ready', 'approved', 'exported')), 0), 2) AS value_usd
            FROM orders WHERE {where} GROUP BY 1 ORDER BY orders DESC""", params).df()


def has_demo_history(con) -> bool:
    return bool(con.execute("SELECT count(*) FROM orders WHERE is_demo").fetchone()[0])


def clear_demo_history(con) -> None:
    con.execute("DELETE FROM issues WHERE is_demo")
    con.execute("DELETE FROM orders WHERE is_demo")


def seed_demo_history(con, weeks: int = 6, seed: int = 7) -> int:
    """Add synthetic past weeks so that the demo dashboard shows trends.

    Every row is marked is_demo = TRUE. The app shows a banner while these rows exist,
    and "Settings > Remove demo history" deletes them.
    """
    rng = random.Random(seed)
    customers = con.execute("SELECT account, name FROM customers").fetchall()
    products = con.execute("SELECT CAST(list_price AS DOUBLE) FROM products").fetchall()
    problem_codes = ["PRICE_MISMATCH", "ITEM_AMBIGUOUS", "QTY_UNUSUAL", "DUPLICATE_ORDER", "ITEM_UNMATCHED",
                     "CUSTOMER_UNKNOWN", "ITEM_INACTIVE"]
    weights = [30, 25, 10, 8, 12, 8, 7]
    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=8, minute=0, second=0, microsecond=0)
    created = 0
    for back in range(weeks, 0, -1):
        start = week_start - timedelta(weeks=back)
        n_orders = rng.randint(150, 220)
        problem_rate = 0.16 - (weeks - back) * 0.018   # the memory list learns, so problems fall
        for _ in range(n_orders):
            account, name = rng.choice(customers)
            received = start + timedelta(days=rng.randint(0, 4), minutes=rng.randint(0, 600))
            n_lines = rng.randint(2, 9)
            total = round(sum(rng.choice(products)[0] * rng.randint(1, 6) for _ in range(n_lines)), 2)
            problem = rng.random() < problem_rate
            review = rng.uniform(45, 150) if problem else 0.0
            status = "exported"
            if problem and rng.random() < 0.06:
                status = "rejected"
            order_id = con.execute("SELECT nextval('order_id_seq')").fetchone()[0]
            con.execute(
                """INSERT INTO orders (id, file_name, source_type, received_at, customer_account, customer_name, status,
                       had_problems, read_by, n_lines, total, review_seconds, is_demo)
                   VALUES (?, 'demo history', 'demo', ?, ?, ?, ?, ?, 'demo', ?, ?, ?, TRUE)""",
                [order_id, received, account, name, status, problem, n_lines, total, review])
            if problem:
                code = rng.choices(problem_codes, weights)[0]
                value = {"PRICE_MISMATCH": rng.uniform(8, 60), "DUPLICATE_ORDER": total,
                         "QTY_UNUSUAL": rng.uniform(200, 2000)}.get(code, 0.0)
                con.execute(
                    """INSERT INTO issues (order_id, code, severity, message, value_usd, resolved, resolution,
                           is_demo, created_at)
                       VALUES (?, ?, 'block', 'demo history', ?, TRUE, 'demo', TRUE, ?)""",
                    [order_id, code, round(value, 2), received])
            created += 1
    return created
