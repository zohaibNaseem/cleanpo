"""Run a free test for a new client: their price list and a few real orders in, results out.

Output folder:
  products_loaded.csv     the product list as CleanPO read it (check it first)
  price_list_problems.csv rows of the price list that were not loaded, or must be checked
  import_<time>.csv       one import file with all orders that are ready
  summary_<time>.xlsx     all orders, lines and problems
  report.md               a one-page summary in plain English

The database is in memory. Copies of the client's files go to a temporary folder that is
deleted at the end, so that only the output folder holds client data.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import duckdb

from . import db, export
from .matcher import Catalog
from .pipeline import ISSUE_TEXT, process_file
from .pricelist import PriceList, load_customers, load_into, read_customer_list, read_price_list
from .reader import Reader

ORDER_SUFFIXES = {".pdf", ".eml", ".xlsx", ".xls", ".csv", ".txt", ".jpg", ".jpeg", ".png"}


@dataclass
class TrialResult:
    out_dir: Path
    price_list: PriceList
    n_customers: int
    orders: list[dict] = field(default_factory=list)
    new_customers: list[tuple[str, str]] = field(default_factory=list)
    import_file: Optional[Path] = None
    report_file: Optional[Path] = None
    api_cost_usd: float = 0.0

    def count(self, status: str) -> int:
        return sum(1 for o in self.orders if o["status"] == status)


def order_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ORDER_SUFFIXES)


def run_trial(orders_dir: str | Path, price_list_path: str | Path, out_dir: str | Path,
              reader: Reader, customers_path: str | Path | None = None,
              price_list_replay_dir: str | Path | None = None) -> TrialResult:
    orders_dir, out_dir = Path(orders_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = order_files(orders_dir)
    if not files:
        raise FileNotFoundError(f"No order files in {orders_dir}")

    price_list = read_price_list(price_list_path, reader, price_list_replay_dir)
    price_list.products.to_csv(out_dir / "products_loaded.csv", index=False)
    price_list.problem_frame().to_csv(out_dir / "price_list_problems.csv", index=False)
    if price_list.products.empty:
        raise ValueError("No products were loaded from the price list. See price_list_problems.csv.")

    con = duckdb.connect(":memory:")
    con.execute(db.SCHEMA)
    load_into(con, price_list)
    customers = None
    if customers_path:
        customers, _ = read_customer_list(customers_path)
    n_customers = load_customers(con, customers)
    catalog = Catalog(con)

    result = TrialResult(out_dir, price_list, n_customers)
    with tempfile.TemporaryDirectory(prefix="cleanpo_trial_") as inbox:
        ids = [process_file(con, path, catalog, reader, inbox, new_customers=True) for path in files]

    for order_id in ids:
        row = con.execute(
            """SELECT file_name, coalesce(customer_name, sender_email, ''), customer_account, coalesce(po_number, ''),
                      n_lines, total, status, api_cost_usd FROM orders WHERE id = ?""", [order_id]).fetchone()
        issues = con.execute(
            "SELECT code, severity, message, line_no, value_usd FROM issues WHERE order_id = ? ORDER BY line_no NULLS FIRST",
            [order_id]).fetchall()
        result.orders.append({
            "id": order_id, "file": row[0], "customer": row[1], "account": row[2], "po": row[3],
            "lines": row[4] or 0, "total": row[5] or 0.0, "status": row[6],
            "issues": [{"code": c, "severity": s, "message": m, "line": l, "value": v or 0.0}
                       for c, s, m, l, v in issues],
        })
        result.api_cost_usd += row[7] or 0.0
    result.api_cost_usd += price_list.cost_usd
    result.new_customers = con.execute(
        "SELECT account, name FROM customers WHERE account LIKE 'NEW-%' ORDER BY account").fetchall()

    stamp = export.file_stamp()
    ready_ids = [o["id"] for o in result.orders if o["status"] == "ready"]
    result.import_file = out_dir / f"import_{stamp}.csv"
    result.import_file.write_bytes(export.import_csv(con, ready_ids))
    facts = {"Orders": len(result.orders), "Ready to import": len(ready_ids),
             "Need a person": result.count("needs_review"), "Not orders": result.count("not_order"),
             "Products loaded": len(price_list.products), "API cost USD": round(result.api_cost_usd, 4)}
    (out_dir / f"summary_{stamp}.xlsx").write_bytes(export.summary_workbook(con, facts))
    result.report_file = out_dir / "report.md"
    result.report_file.write_text(report_text(result, reader), encoding="utf-8")
    con.close()
    return result


def report_text(result: TrialResult, reader: Reader) -> str:
    pl = result.price_list
    how = {"columns": "read from the columns (no AI)", "claude": f"read by Claude ({reader.model})",
           "replay": "saved demo reading"}[pl.method]
    caught = sum(i["value"] for o in result.orders for i in o["issues"] if i["severity"] == "block")
    out = [
        "# Free test result",
        "",
        f"Price list: {pl.source}, {len(pl.products)} products loaded, {how}.",
        f"Rows not loaded: {pl.skipped}. Section headings skipped: {pl.headings_skipped}.",
        f"Customer list: {result.n_customers} customers"
        + (" (no list sent: senders were added as new customers)." if not result.n_customers else "."),
        "",
        f"Orders: {len(result.orders)} files. Ready to import: {result.count('ready')}. "
        f"Need a person: {result.count('needs_review')}. Not orders: {result.count('not_order')}.",
        f"Value of the problems found before entry: ${caught:,.2f}.",
        f"Reading: {'Claude ' + reader.model if reader.uses_ai else 'saved demo readings (no API key)'}. "
        f"API cost of this test: ${result.api_cost_usd:,.4f}.",
        "",
        "## Ready to import",
        "",
    ]
    ready = [o for o in result.orders if o["status"] == "ready"]
    if ready:
        out += ["| File | Customer | PO | Lines | Total |", "|---|---|---|---|---|"]
        out += [f"| {o['file']} | {o['customer']} | {o['po']} | {o['lines']} | ${o['total']:,.2f} |" for o in ready]
    else:
        out.append("None.")
    out += ["", "## Need a person", ""]
    review = [o for o in result.orders if o["status"] == "needs_review"]
    if not review:
        out.append("None.")
    for o in review:
        out.append(f"**{o['file']}** ({o['customer'] or 'unknown customer'})")
        for i in o["issues"]:
            if i["severity"] == "block":
                where = f"line {i['line']}: " if i["line"] else ""
                out.append(f"- {ISSUE_TEXT.get(i['code'], i['code'])}. {where}{i['message']}")
        out.append("")
    not_orders = [o for o in result.orders if o["status"] == "not_order"]
    if not_orders:
        out += ["## Not orders (kept out of the import file)", ""]
        out += [f"- {o['file']}" for o in not_orders]
        out.append("")
    if pl.problems:
        out += ["## Price list: rows to check", ""]
        out += [f"- {'Row ' + str(p.row) + ': ' if p.row else ''}{p.message}"
                f"{'' if p.severity == 'info' else ' Not loaded.'}" for p in pl.problems]
        out.append("")
    if result.new_customers:
        out += ["## Customers added for this test", ""]
        out += [f"- {account}: {name}" for account, name in result.new_customers]
        out += ["", "One customer can show twice, when one order gives only a name and another gives only "
                    "an e-mail address. Check this list before you send the result.", ""]
    out += ["## Notes", "",
            "- Prices are checked against the standard prices in the price list. Special prices for "
            "single customers are not known in this test, so such orders show a price difference.",
            "- Quantity checks use a fixed limit, because the order history is not known in this test.",
            "- The import file uses a generic layout. For a real setup it is changed to the layout of "
            "your system."]
    return "\n".join(out) + "\n"
