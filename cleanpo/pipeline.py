"""Process one order file: read, match, check, sort. Also approve and reject after review."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import duckdb

from .matcher import UNIT_WORDS, Catalog, CustomerMatch, normalize_unit
from .reader import ReadError, Reader

PRICE_TOLERANCE = 0.01        # 1 % difference from the price list is accepted
UNUSUAL_FACTOR = 3            # more than 3 x the largest earlier quantity
UNUSUAL_NO_HISTORY = 100      # no history: more than this quantity is unusual
DUPLICATE_DAYS = 3
KNOWN_UNITS = set(UNIT_WORDS.values())   # selling and pack units; sizes such as "lb" are not in it

ISSUE_TEXT = {
    "CUSTOMER_UNKNOWN": "Customer not found in the customer list",
    "CUSTOMER_ADDED": "New customer added for this test",
    "CREDIT_HOLD": "Customer account is on credit hold",
    "ITEM_AMBIGUOUS": "More than one product fits",
    "ITEM_UNMATCHED": "No product found",
    "ITEM_INACTIVE": "Product is discontinued",
    "PRICE_MISMATCH": "Price differs from the price list",
    "QTY_UNUSUAL": "Quantity is much larger than usual",
    "UNIT_MISMATCH": "Unit does not fit the product pack",
    "UNIT_CONVERTED": "Unit converted",
    "DUPLICATE_ORDER": "Possible duplicate order",
    "NOT_AN_ORDER": "Not an order",
    "READ_FAILED": "Could not read the file",
    "NO_LINES": "No order lines found",
}


@dataclass
class Issue:
    code: str
    severity: str           # block | info
    message: str
    line_no: Optional[int] = None
    value_usd: float = 0.0


def _status(issues: list[Issue], is_order: bool) -> str:
    if not is_order:
        return "not_order"
    return "needs_review" if any(i.severity == "block" for i in issues) else "ready"


def process_file(con: duckdb.DuckDBPyConnection, path: str | Path, catalog: Catalog, reader: Reader,
                 inbox_dir: str | Path, new_customers: bool = False) -> int:
    """Process one file and save the result. Return the new order id.

    new_customers: add a sender that is not in the customer list as a new customer, instead of
    stopping the order. Use it for a free test, when the client did not send a customer list.
    """
    path = Path(path)
    order_id = con.execute("SELECT nextval('order_id_seq')").fetchone()[0]
    inbox_dir = Path(inbox_dir)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    stored = inbox_dir / f"{order_id:05d}_{path.name}"
    shutil.copyfile(path, stored)

    try:
        result = reader.read(path)
    except ReadError as exc:
        _save_failed(con, order_id, path, stored, str(exc))
        return order_id
    except Exception as exc:  # API or network error: keep the file for a human
        _save_failed(con, order_id, path, stored, f"{type(exc).__name__}: {exc}")
        return order_id

    reading = result.reading
    issues: list[Issue] = []
    customer = catalog.match_customer(result.sender_email, reading.customer_email, reading.customer_name)
    api_cost = result.cost_usd

    if not reading.is_order:
        issues.append(Issue("NOT_AN_ORDER", "info", reading.notes or "This message is not an order."))
    else:
        who = reading.customer_name or result.sender_email or reading.customer_email or "unknown sender"
        email_used = result.sender_email or reading.customer_email
        if not customer.account and new_customers and (reading.customer_name or email_used):
            account = catalog.add_customer(reading.customer_name, email_used)
            customer = CustomerMatch(account, reading.customer_name or email_used, "new")
            issues.append(Issue("CUSTOMER_ADDED", "info", f"'{who}' was not in the customer list. "
                                                          f"Added as {account} for this test."))
        if not customer.account:
            issues.append(Issue("CUSTOMER_UNKNOWN", "block", f"'{who}' is not in the customer list. New customer?"))
        elif customer.credit_hold:
            issues.append(Issue("CREDIT_HOLD", "block", f"{customer.name} ({customer.account}) is on credit hold. "
                                                         "A manager must approve."))
        if not reading.lines:
            issues.append(Issue("NO_LINES", "block", "The order has no product lines."))

    lines = []
    for no, line in enumerate(reading.lines, start=1):
        match = catalog.match_product(line.description, line.item_code, customer.account)
        method = match.method
        if match.code is None and match.ambiguous and reader.uses_ai:
            code, reason, cost = reader.choose_product(
                line.raw_text, customer.name or "unknown",
                [(c, n) for c, n, _ in match.candidates], catalog.history_names(customer.account))
            api_cost += cost
            if code:
                match.code, method = code, "ai"

        qty, unit = line.quantity, normalize_unit(line.unit)
        price = amount = None
        product = catalog.products.get(match.code) if match.code else None
        candidates_txt = ", ".join(f"{c} {n}" for c, n, _ in match.candidates[:3])

        if product is None:
            if match.ambiguous:
                issues.append(Issue("ITEM_AMBIGUOUS", "block",
                                    f"'{line.description}' fits {len(match.candidates)} products: {candidates_txt}.", no))
            else:
                hint = f" Closest: {candidates_txt}." if match.candidates else ""
                issues.append(Issue("ITEM_UNMATCHED", "block", f"'{line.description}' is not in the product list.{hint}", no))
        else:
            unit_out = product.unit
            if unit and unit == product.pack_unit and product.pack_size > 1 and unit != product.unit:
                if qty % product.pack_size == 0:
                    new_qty = qty / product.pack_size
                    issues.append(Issue("UNIT_CONVERTED", "info",
                                        f"{qty:g} {unit} = {new_qty:g} {product.unit} ({product.pack_size} per {product.unit}).", no))
                    qty = new_qty
                else:
                    issues.append(Issue("UNIT_MISMATCH", "block",
                                        f"{qty:g} {unit} is not a full {product.unit} ({product.pack_size} per {product.unit}).", no))
            elif (unit and unit != "case" and unit not in (product.unit, product.pack_unit)
                  and unit in KNOWN_UNITS):
                # For example "60 can" when the product list does not say how many cans are in a case.
                # "case" is not flagged: customers often write "cases" for items sold one by one.
                issues.append(Issue("UNIT_MISMATCH", "block",
                                    f"Ordered as {qty:g} {unit}, but {product.name} is sold per {product.unit}. "
                                    "Check the quantity.", no))
            unit = unit_out
            if not product.active:
                issues.append(Issue("ITEM_INACTIVE", "block", f"{product.code} {product.name} is discontinued. "
                                                               "Choose a replacement or remove the line.", no))
            price = catalog.price_for(product.code, customer.account)
            if line.unit_price is not None and price and abs(line.unit_price - price) > PRICE_TOLERANCE * price:
                diff = (price - line.unit_price) * qty
                issues.append(Issue("PRICE_MISMATCH", "block",
                                    f"{product.name}: order says ${line.unit_price:,.2f}, price list says ${price:,.2f}.",
                                    no, value_usd=round(abs(diff), 2)))
            usual = catalog.usual_qty(customer.account, product.code)
            limit = usual * UNUSUAL_FACTOR if usual else UNUSUAL_NO_HISTORY
            if qty > limit:
                why = f"usually at most {usual:g}" if usual else "no earlier orders of this size"
                issues.append(Issue("QTY_UNUSUAL", "block", f"{qty:g} {unit} of {product.name} ({why}). Typing mistake?", no,
                                    value_usd=round((qty - (usual or 0)) * (price or 0), 2)))
            amount = round(qty * (price or 0), 2)

        lines.append({
            "line_no": no, "raw_text": line.raw_text, "description": line.description,
            "item_code_written": line.item_code, "qty_written": line.quantity, "unit_written": line.unit,
            "unit_price_written": line.unit_price, "qty": qty, "unit": unit,
            "product_code": product.code if product else None, "product_name": product.name if product else None,
            "match_method": method if product else "none", "match_score": match.score,
            "candidates": json.dumps(match.candidates), "price": price, "amount": amount,
        })

    total = round(sum(l["amount"] or 0 for l in lines), 2)
    if reading.is_order and customer.account:
        dup = _find_duplicate(con, customer.account, reading.po_number, lines, order_id)
        if dup:
            issues.append(Issue("DUPLICATE_ORDER", "block", dup, value_usd=total))

    status = _status(issues, reading.is_order)
    con.execute(
        """INSERT INTO orders (id, file_name, stored_path, source_type, customer_account, customer_name,
               sender_email, po_number, order_date, delivery_date, status, had_problems, read_by, model,
               input_tokens, output_tokens, api_cost_usd, n_lines, total, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [order_id, path.name, str(stored), result.source_type, customer.account,
         customer.name or reading.customer_name, result.sender_email or reading.customer_email,
         reading.po_number, reading.order_date, reading.requested_delivery_date, status,
         status == "needs_review", result.read_by, result.model, result.input_tokens, result.output_tokens,
         round(api_cost, 6), len(lines), total, reading.notes],
    )
    for l in lines:
        con.execute(
            """INSERT INTO order_lines (order_id, line_no, raw_text, description, item_code_written, qty_written,
                   unit_written, unit_price_written, qty, unit, product_code, product_name, match_method,
                   match_score, candidates, price, amount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [order_id, l["line_no"], l["raw_text"], l["description"], l["item_code_written"], l["qty_written"],
             l["unit_written"], l["unit_price_written"], l["qty"], l["unit"], l["product_code"], l["product_name"],
             l["match_method"], l["match_score"], l["candidates"], l["price"], l["amount"]],
        )
    _save_issues(con, order_id, issues)
    return order_id


def _find_duplicate(con, account: str, po: Optional[str], lines: list[dict], order_id: int) -> Optional[str]:
    if po:
        row = con.execute(
            """SELECT id, file_name FROM orders WHERE customer_account = ? AND po_number = ? AND id <> ?
               AND status NOT IN ('rejected', 'not_order') AND NOT is_demo ORDER BY id LIMIT 1""",
            [account, po, order_id]).fetchone()
        if row:
            return f"PO {po} from this customer was already received (order #{row[0]}, {row[1]})."
    signature = sorted((l["product_code"] or l["description"], l["qty"]) for l in lines)
    recent = con.execute(
        f"""SELECT id, file_name FROM orders WHERE customer_account = ? AND id <> ?
            AND status NOT IN ('rejected', 'not_order') AND NOT is_demo
            AND received_at > current_timestamp - INTERVAL {DUPLICATE_DAYS} DAY""",
        [account, order_id]).fetchall()
    for other_id, file_name in recent:
        other = sorted(
            (code or desc, qty) for code, desc, qty in con.execute(
                "SELECT product_code, description, qty FROM order_lines WHERE order_id = ? AND NOT removed",
                [other_id]).fetchall())
        if other and other == signature:
            return f"Same products and quantities as order #{other_id} ({file_name}) from the last {DUPLICATE_DAYS} days."
    return None


def _save_issues(con, order_id: int, issues: list[Issue]) -> None:
    for i in issues:
        con.execute(
            "INSERT INTO issues (order_id, line_no, code, severity, message, value_usd) VALUES (?, ?, ?, ?, ?, ?)",
            [order_id, i.line_no, i.code, i.severity, i.message, i.value_usd])


def _save_failed(con, order_id: int, path: Path, stored: Path, reason: str) -> None:
    con.execute(
        """INSERT INTO orders (id, file_name, stored_path, source_type, status, had_problems, notes)
           VALUES (?, ?, ?, ?, 'needs_review', TRUE, ?)""",
        [order_id, path.name, str(stored), path.suffix.lower().lstrip("."), reason])
    _save_issues(con, order_id, [Issue("READ_FAILED", "block", reason)])


# ---------------------------------------------------------------------------
# After review
# ---------------------------------------------------------------------------

def approve(con: duckdb.DuckDBPyConnection, catalog: Catalog, order_id: int, *,
            customer_account: Optional[str] = None, line_changes: Optional[dict[int, dict]] = None,
            remember: bool = True, review_seconds: float = 0.0, note: str = "") -> None:
    """Apply the reviewer's changes, mark all problems as solved and set the order to approved.

    line_changes: {line_no: {"product_code": ..., "qty": ..., "price": ..., "removed": bool}}
    """
    line_changes = line_changes or {}
    if customer_account:
        name = next((c[1] for c in catalog.customers if c[0] == customer_account), None)
        con.execute("UPDATE orders SET customer_account = ?, customer_name = ? WHERE id = ?",
                    [customer_account, name, order_id])
    account = con.execute("SELECT customer_account FROM orders WHERE id = ?", [order_id]).fetchone()[0]

    for line_no, change in line_changes.items():
        row = con.execute(
            "SELECT description, product_code, qty, price FROM order_lines WHERE order_id = ? AND line_no = ?",
            [order_id, line_no]).fetchone()
        if not row:
            continue
        description, old_code, qty, price = row
        if change.get("removed"):
            con.execute("UPDATE order_lines SET removed = TRUE, amount = 0 WHERE order_id = ? AND line_no = ?",
                        [order_id, line_no])
            continue
        code = change.get("product_code") or old_code
        qty = float(change.get("qty", qty) or 0)
        if code and code != old_code:
            price = catalog.price_for(code, account)
            if remember and description:
                con.execute("INSERT INTO learned_aliases (customer_account, written_as, product_code) VALUES (?, ?, ?)",
                            [account or "", description, code])
        if "price" in change and change["price"] is not None:
            price = float(change["price"])
        product = catalog.products.get(code) if code else None
        con.execute(
            """UPDATE order_lines SET product_code = ?, product_name = ?, unit = coalesce(?, unit), qty = ?, price = ?,
                   amount = ?, match_method = CASE WHEN ? <> coalesce(product_code, '') THEN 'human' ELSE match_method END
               WHERE order_id = ? AND line_no = ?""",
            [code, product.name if product else None, product.unit if product else None, qty, price,
             round(qty * (price or 0), 2), code or "", order_id, line_no])

    total = con.execute(
        "SELECT coalesce(sum(amount), 0), count(*) FILTER (WHERE NOT removed) FROM order_lines WHERE order_id = ?",
        [order_id]).fetchone()
    con.execute(
        """UPDATE orders SET status = 'approved', total = ?, n_lines = ?, review_seconds = review_seconds + ?,
               reviewed_at = current_timestamp WHERE id = ?""",
        [round(total[0], 2), total[1], review_seconds, order_id])
    con.execute("UPDATE issues SET resolved = TRUE, resolution = ? WHERE order_id = ? AND NOT resolved",
                [note or "approved after review", order_id])
    catalog.reload_aliases()


def reject(con: duckdb.DuckDBPyConnection, order_id: int, review_seconds: float = 0.0, note: str = "") -> None:
    con.execute(
        """UPDATE orders SET status = 'rejected', review_seconds = review_seconds + ?, reviewed_at = current_timestamp
           WHERE id = ?""", [review_seconds, order_id])
    con.execute("UPDATE issues SET resolved = TRUE, resolution = ? WHERE order_id = ? AND NOT resolved",
                [note or "rejected", order_id])
