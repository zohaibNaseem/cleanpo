"""End-to-end test: process the 16 demo orders in replay mode and compare with expected.json."""

import json
from pathlib import Path

import duckdb
import pytest

from cleanpo import db
from cleanpo.matcher import Catalog, normalize
from cleanpo.pipeline import approve, process_file
from cleanpo.reader import Reader

DEMO = Path(__file__).resolve().parents[1] / "demo_data"
EXPECTED = json.loads((DEMO / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    con = duckdb.connect(":memory:")
    con.execute(db.SCHEMA)
    db.load_master(con, DEMO / "master")
    catalog = Catalog(con)
    reader = Reader(mode="replay", replay_dir=DEMO / "readings")
    inbox = tmp_path_factory.mktemp("inbox")
    ids = {}
    for item in EXPECTED:
        ids[item["file"]] = process_file(con, DEMO / "orders" / item["file"], catalog, reader, inbox)
    return con, catalog, ids


@pytest.mark.parametrize("item", EXPECTED, ids=[e["file"] for e in EXPECTED])
def test_status_and_issues(processed, item):
    con, _, ids = processed
    order_id = ids[item["file"]]
    status = con.execute("SELECT status FROM orders WHERE id = ?", [order_id]).fetchone()[0]
    codes = sorted(r[0] for r in con.execute("SELECT code FROM issues WHERE order_id = ?", [order_id]).fetchall())
    assert status == item["status"], item["story"]
    assert codes == item["issue_codes"], item["story"]


def test_unit_conversion(processed):
    con, _, ids = processed
    qty, unit = con.execute(
        "SELECT qty, unit FROM order_lines WHERE order_id = ? AND line_no = 1",
        [ids["14_taqueria_el_sol_email.eml"]]).fetchone()
    assert (qty, unit) == (10, "case")


def test_customer_price_used(processed):
    con, _, ids = processed
    price = con.execute(
        "SELECT price FROM order_lines WHERE order_id = ? AND product_code = 'DAI-110'",
        [ids["01_tonys_po_1001.pdf"]]).fetchone()[0]
    assert price == 64.90  # special price for C-1001, list price is 68.40


def test_review_teaches_memory(processed):
    con, catalog, ids = processed
    order_id = ids["13_campus_corner_email.eml"]
    approve(con, catalog, order_id, line_changes={1: {"product_code": "OIL-110"}}, review_seconds=40)
    assert con.execute("SELECT status FROM orders WHERE id = ?", [order_id]).fetchone()[0] == "approved"
    match = catalog.match_product("olive oil 5L", None, "C-1015")
    assert (match.code, match.method) == ("OIL-110", "memory")


@pytest.mark.parametrize("text,expected", [
    ("2 bags AP flour (50lb)", "2 bags all purpose flour 50lb"),
    ("russet potatoes 50#", "russet potatoes 50lb"),
    ("Extra virgin olive oil 5 liter", "extra virgin olive oil 5l"),
])
def test_normalize(text, expected):
    assert normalize(text) == expected
