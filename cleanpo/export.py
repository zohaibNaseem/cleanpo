"""Output files: the import file for the ERP and a daily summary workbook.

The import layout below is a generic sales-order CSV. During setup for a real client it is
changed to the exact layout that their software imports.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Iterable

import duckdb
import pandas as pd

IMPORT_COLUMNS = ["OrderRef", "CustomerAccount", "CustomerName", "PONumber", "OrderDate", "ShipDate",
                  "LineNo", "ItemCode", "Description", "Qty", "Unit", "UnitPrice", "Amount"]


def exportable_ids(con: duckdb.DuckDBPyConnection) -> list[int]:
    return [r[0] for r in con.execute(
        "SELECT id FROM orders WHERE status IN ('ready', 'approved') AND NOT is_demo ORDER BY id").fetchall()]


def import_frame(con: duckdb.DuckDBPyConnection, order_ids: Iterable[int]) -> pd.DataFrame:
    ids = list(order_ids)
    if not ids:
        return pd.DataFrame(columns=IMPORT_COLUMNS)
    frame = con.execute(
        f"""SELECT 'CPO-' || lpad(CAST(o.id AS VARCHAR), 5, '0') AS OrderRef,
                   o.customer_account AS CustomerAccount, o.customer_name AS CustomerName,
                   coalesce(o.po_number, '') AS PONumber, coalesce(o.order_date, '') AS OrderDate,
                   coalesce(o.delivery_date, '') AS ShipDate,
                   row_number() OVER (PARTITION BY o.id ORDER BY l.line_no) AS LineNo,
                   l.product_code AS ItemCode, l.product_name AS Description, l.qty AS Qty, l.unit AS Unit,
                   round(l.price, 2) AS UnitPrice, round(l.amount, 2) AS Amount
            FROM orders o JOIN order_lines l ON l.order_id = o.id
            WHERE o.id IN ({",".join("?" * len(ids))}) AND NOT l.removed
            ORDER BY o.id, l.line_no""", ids).df()
    return frame[IMPORT_COLUMNS]


def import_csv(con: duckdb.DuckDBPyConnection, order_ids: Iterable[int]) -> bytes:
    return import_frame(con, order_ids).to_csv(index=False).encode("utf-8")


def mark_exported(con: duckdb.DuckDBPyConnection, order_ids: Iterable[int]) -> None:
    ids = list(order_ids)
    if ids:
        con.execute(
            f"UPDATE orders SET status = 'exported', exported_at = current_timestamp "
            f"WHERE id IN ({','.join('?' * len(ids))})", ids)


def summary_workbook(con: duckdb.DuckDBPyConnection, facts: dict) -> bytes:
    """An Excel file with the summary, all orders, all lines and all problems (demo history excluded)."""
    orders = con.execute(
        """SELECT id AS "Order", file_name AS "File", received_at AS "Received", customer_account AS "Account",
                  customer_name AS "Customer", po_number AS "PO", status AS "Status", n_lines AS "Lines",
                  total AS "Total USD", read_by AS "Read by", round(review_seconds / 60, 1) AS "Review minutes"
           FROM orders WHERE NOT is_demo ORDER BY id""").df()
    lines = con.execute(
        """SELECT order_id AS "Order", line_no AS "Line", raw_text AS "As written", product_code AS "Item",
                  product_name AS "Product", qty AS "Qty", unit AS "Unit", price AS "Price", amount AS "Amount",
                  match_method AS "Matched by", removed AS "Removed"
           FROM order_lines WHERE order_id IN (SELECT id FROM orders WHERE NOT is_demo)
           ORDER BY order_id, line_no""").df()
    issues = con.execute(
        """SELECT order_id AS "Order", line_no AS "Line", code AS "Problem", severity AS "Type",
                  message AS "Details", value_usd AS "Value at risk USD", resolved AS "Solved",
                  resolution AS "How"
           FROM issues WHERE NOT is_demo ORDER BY order_id, line_no""").df()
    summary = pd.DataFrame([{"Measure": k, "Value": v} for k, v in facts.items()])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        orders.to_excel(writer, sheet_name="Orders", index=False)
        lines.to_excel(writer, sheet_name="Lines", index=False)
        issues.to_excel(writer, sheet_name="Problems", index=False)
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                width = max(len(str(c.value or "")) for c in column[:200])
                sheet.column_dimensions[column[0].column_letter].width = min(max(10, width + 2), 60)
    return buffer.getvalue()


def file_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")
