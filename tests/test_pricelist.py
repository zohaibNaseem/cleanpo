"""Tests for the price-list import, the free-test runner and the related pipeline checks."""

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from cleanpo import db
from cleanpo.matcher import Catalog
from cleanpo.pipeline import process_file
from cleanpo.pricelist import (PriceListError, load_customers, load_into, parse_pack, parse_price,
                               read_customer_list, read_price_list)
from cleanpo.reader import OrderLine, OrderReading, Reader, ReadResult
from cleanpo.trial import run_trial

DEMO = Path(__file__).resolve().parents[1] / "demo_data"
LISTS = DEMO / "price_lists"
REPLAY = LISTS / "readings"
MASTER = pd.read_csv(DEMO / "master" / "products.csv", dtype=str)
FIELDS = ["name", "unit", "pack_size", "pack_unit", "list_price", "active"]


def differences(products: pd.DataFrame, fields=FIELDS) -> list:
    merged = products.merge(MASTER, on="code", suffixes=("", "_master"))
    assert len(merged) == len(products), "every code must exist in the master list"
    return [(row["code"], f, row[f], row[f + "_master"])
            for _, row in merged.iterrows() for f in fields if row[f] != row[f + "_master"]]


def test_excel_with_title_rows_headings_and_dollar_prices():
    pl = read_price_list(LISTS / "lone_pine_price_list.xlsx")
    assert pl.method == "columns"
    assert len(pl.products) == len(MASTER) == 152
    assert pl.headings_skipped == 12          # 11 section headings and 1 footer line
    assert pl.problems == []
    assert pl.columns == {"code": "Item #", "name": "Description", "unit": "U/M", "pack": "Pack/Size",
                          "price": "Case Price", "status": "Status"}
    assert differences(pl.products) == []


def test_csv_export_with_planted_defects():
    pl = read_price_list(LISTS / "vendor_export.csv")
    assert len(pl.products) == 38
    assert [(p.row, p.code) for p in pl.problems] == [(7, "NO_PRICE"), (12, "NO_CODE"), (42, "DUPLICATE_CODE")]
    first = pl.products.set_index("code").loc["CAN-110"]
    assert first["list_price"] == "42.50"     # the first row wins over the duplicate at 45.00
    # The export has no inner-unit column. The pack unit stays empty unless the name ends in a
    # container word, so that no wrong unit conversion is possible.
    wrong = [d for d in differences(pl.products) if d[1] == "pack_unit" and d[2] != ""]
    assert wrong == []
    assert [d for d in differences(pl.products) if d[1] != "pack_unit"] == []


def test_list_without_codes_gets_made_codes():
    pl = read_price_list(LISTS / "simple_list_no_codes.xlsx")
    assert len(pl.products) == 20
    assert list(pl.products["code"][:2]) == ["P-0001", "P-0002"]
    assert pl.problems[0].code == "CODES_MADE" and pl.problems[0].severity == "info"
    assert pl.skipped == 0


def test_pdf_replay_reading():
    pl = read_price_list(LISTS / "price_list_print.pdf", None, REPLAY)
    assert pl.method == "replay"
    assert len(pl.products) == 30
    assert differences(pl.products) == []
    assert pl.products.set_index("code").loc["CAN-240", "active"] == "no"


def test_pdf_without_api_key_stops_with_a_clear_message():
    with pytest.raises(PriceListError, match="needs the Claude API"):
        read_price_list(LISTS / "price_list_print.pdf", Reader(mode="replay"), replay_dir=None)


def test_spreadsheet_without_price_column_needs_the_api(tmp_path):
    path = tmp_path / "notes.csv"
    path.write_text("Item,Remarks\nA1,call me\n", encoding="utf-8")
    with pytest.raises(PriceListError, match="could not find"):
        read_price_list(path)


def test_discontinued_column_means_not_active(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("Item No,Item Description,Price,Discontinued\nA1,Widget,10,Yes\nA2,Gadget,$12.50,\n",
                    encoding="utf-8")
    products = read_price_list(path).products.set_index("code")
    assert products.loc["A1", "active"] == "no"
    assert products.loc["A2", "active"] == "yes"
    assert products.loc["A2", "list_price"] == "12.50"


@pytest.mark.parametrize("text,expected", [("$1,234.50", 1234.5), ("42", 42.0), ("", None), ("call", None)])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("pack,name,expected", [
    ("6/#10", "Tomato Paste #10 Can", (6, "can")),
    ("8/LOAF", "Mozzarella Loaf", (8, "loaf")),
    ("36/LB", "Butter 1lb Solid", (36, "lb")),
    ("1/CASE", "Pepperoni Sliced 25lb Case", (1, "case")),
    ("12 ct", "Romaine Hearts", (12, "")),
    ("4", "Mozzarella Shredded 5lb Bag", (4, "bag")),
    ("6", "Heavy Cream 40% Half Gallon", (6, "")),    # a measure, not a container: no guess
])
def test_parse_pack(pack, name, expected):
    assert parse_pack(pack, name) == expected


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_final_message(self):
        return self.response


def fake_reader(payload: dict, stop_reason: str = "end_turn") -> Reader:
    response = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500))
    reader = Reader(mode="replay")
    reader.mode = "claude"
    reader.model = "claude-opus-5-5"
    reader._client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: FakeStream(response)))
    return reader


def test_claude_reading_goes_through_the_same_checks():
    payload = {"items": [
        {"item_code": "X1", "description": "Flour 50lb Bag", "unit": "EA", "pack": None, "price": 21.4,
         "discontinued": False},
        {"item_code": "X2", "description": "Sugar 50lb Bag", "unit": None, "pack": None, "price": None,
         "discontinued": False},
    ], "notes": "Two price columns; the case price was used."}
    pl = read_price_list(LISTS / "price_list_print.pdf", fake_reader(payload))
    assert pl.method == "claude"
    assert list(pl.products["code"]) == ["X1"]
    assert [p.code for p in pl.problems] == ["READER_NOTE", "NO_PRICE"]
    assert pl.cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1_000_000)


def test_claude_reading_cut_off_asks_to_split_the_file():
    reader = fake_reader({"items": [], "notes": None}, stop_reason="max_tokens")
    with pytest.raises(PriceListError, match="Split the file"):
        read_price_list(LISTS / "price_list_print.pdf", reader)


def test_customer_list_in_client_layout():
    customers, problems = read_customer_list(LISTS / "customer_list.xlsx")
    assert len(customers) == 20 and problems == []
    assert customers.iloc[0][["account", "email"]].tolist() == ["C-1001", "orders@tonyspizza.example"]


# ----- Pipeline checks used by the free test --------------------------------

class OneReading:
    """A reader that returns one fixed reading."""
    uses_ai = False

    def __init__(self, reading: OrderReading, sender: str | None = None):
        self.reading, self.sender = reading, sender

    def read(self, path):
        return ReadResult(reading=self.reading, source_type="email", sender_email=self.sender)


def small_catalog(tmp_path) -> tuple[duckdb.DuckDBPyConnection, Catalog]:
    path = tmp_path / "list.csv"
    path.write_text("SKU,Description,Pack,Price\nT1,Tomato Paste,6,42.50\n", encoding="utf-8")
    con = duckdb.connect(":memory:")
    con.execute(db.SCHEMA)
    load_into(con, read_price_list(path))
    load_customers(con, None)
    return con, Catalog(con)


def order(unit: str, qty: float) -> OrderReading:
    return OrderReading(is_order=True, customer_name="Cafe One", lines=[
        OrderLine(raw_text=f"{qty} {unit} tomato paste", description="tomato paste", quantity=qty, unit=unit)])


def issue_codes(con, order_id):
    return sorted(r[0] for r in con.execute("SELECT code FROM issues WHERE order_id = ?", [order_id]).fetchall())


def test_container_unit_without_known_pack_unit_is_flagged(tmp_path):
    con, catalog = small_catalog(tmp_path)
    order_file = tmp_path / "o.eml"
    order_file.write_text("x", encoding="utf-8")
    order_id = process_file(con, order_file, catalog, OneReading(order("cans", 60)), tmp_path / "in",
                            new_customers=True)
    assert issue_codes(con, order_id) == ["CUSTOMER_ADDED", "UNIT_MISMATCH"]
    order_id = process_file(con, order_file, catalog, OneReading(order("cases", 2)), tmp_path / "in",
                            new_customers=True)
    assert "UNIT_MISMATCH" not in issue_codes(con, order_id)


def test_shared_mail_domain_does_not_identify_a_customer(tmp_path):
    con, catalog = small_catalog(tmp_path)
    catalog.add_customer("Cafe One", "cafeone@gmail.com")
    assert catalog.match_customer("otherplace@gmail.com", None, None).account is None
    assert catalog.match_customer("cafeone@gmail.com", None, None).account == "NEW-001"


def test_new_customers_are_reused_for_the_next_order(tmp_path):
    con, catalog = small_catalog(tmp_path)
    order_file = tmp_path / "o.eml"
    order_file.write_text("x", encoding="utf-8")
    reader = OneReading(order("cases", 2), sender="buyer@cafeone.example")
    first = process_file(con, order_file, catalog, reader, tmp_path / "in", new_customers=True)
    second = process_file(con, order_file, catalog, reader, tmp_path / "in", new_customers=True)
    accounts = [con.execute("SELECT customer_account FROM orders WHERE id = ?", [i]).fetchone()[0]
                for i in (first, second)]
    assert accounts == ["NEW-001", "NEW-001"]
    assert "DUPLICATE_ORDER" in issue_codes(con, second)


def test_without_the_option_an_unknown_sender_still_stops_the_order(tmp_path):
    con, catalog = small_catalog(tmp_path)
    order_file = tmp_path / "o.eml"
    order_file.write_text("x", encoding="utf-8")
    order_id = process_file(con, order_file, catalog, OneReading(order("cases", 2)), tmp_path / "in")
    assert issue_codes(con, order_id) == ["CUSTOMER_UNKNOWN"]


# ----- Free-test runner ------------------------------------------------------

def test_free_test_end_to_end(tmp_path):
    reader = Reader(mode="replay", replay_dir=DEMO / "readings")
    result = run_trial(DEMO / "orders", LISTS / "lone_pine_price_list.xlsx", tmp_path, reader)
    assert len(result.price_list.products) == 152
    assert (result.count("ready"), result.count("needs_review"), result.count("not_order")) == (8, 7, 1)
    flagged = {o["file"]: sorted({i["code"] for i in o["issues"] if i["severity"] == "block"})
               for o in result.orders if o["status"] == "needs_review"}
    assert flagged["08_bella_napoli_old_price.pdf"] == ["PRICE_MISMATCH"]
    assert flagged["09_tonys_po_1001_resent.pdf"] == ["DUPLICATE_ORDER", "PRICE_MISMATCH"]
    assert flagged["12_harbor_grill_discontinued.pdf"] == ["ITEM_INACTIVE"]
    assert flagged["10_sunrise_bakery_email.eml"] == ["QTY_UNUSUAL"]
    imported = pd.read_csv(result.import_file)
    assert imported["OrderRef"].nunique() == 8
    assert {"products_loaded.csv", "price_list_problems.csv", "report.md"} <= {p.name for p in tmp_path.iterdir()}
    assert "Ready to import: 8" in result.report_file.read_text(encoding="utf-8")
    assert result.api_cost_usd == 0


def test_free_test_with_a_customer_list(tmp_path):
    reader = Reader(mode="replay", replay_dir=DEMO / "readings")
    result = run_trial(DEMO / "orders", LISTS / "lone_pine_price_list.xlsx", tmp_path, reader,
                       customers_path=LISTS / "customer_list.xlsx")
    assert result.n_customers == 20
    assert [name for _, name in result.new_customers] == ["Lucky Dragon Kitchen"]
