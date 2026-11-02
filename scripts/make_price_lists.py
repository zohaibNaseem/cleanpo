"""Make synthetic price lists in different client layouts, for the price-list import tests.

All data is made up. It is built from demo_data/master/products.csv and customers.csv, so
run scripts/make_demo_data.py first.

Output (in demo_data/price_lists/):
  lone_pine_price_list.xlsx  all products: title rows, section headings, "$" prices, Disc status
  vendor_export.csv          40 products from a system export, with 3 planted defects
  simple_list_no_codes.xlsx  20 products with a name and a price only (no item codes)
  price_list_print.pdf       30 products in a printed PDF (read by Claude, or replay)
  readings/price_list_print.pdf.json  what a correct reader returns for the PDF (replay mode)
  customer_list.xlsx         the customers, in a client layout

Run:  python scripts/make_price_lists.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1] / "demo_data"
MASTER = ROOT / "master"
OUT = ROOT / "price_lists"
READINGS = OUT / "readings"

SECTIONS = {"CAN": "CANNED GOODS", "OIL": "OILS AND VINEGAR", "DAI": "DAIRY", "DRY": "DRY GOODS",
            "MEA": "MEAT", "FRZ": "FROZEN", "PRO": "PRODUCE", "BEV": "BEVERAGES",
            "PAP": "PAPER AND DISPOSABLES", "CHM": "CHEMICALS AND JANITORIAL"}
UM = {"case": "CS", "each": "EA", "bundle": "BDL"}

# Planted defects in vendor_export.csv (index in the first 40 products).
NO_PRICE_INDEX = 5
NO_CODE_INDEX = 10
DUPLICATE_INDEX = 0


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def pack_text(p: dict) -> str:
    if "#10" in p["name"]:
        return f"{p['pack_size']}/#10"
    return f"{p['pack_size']}/{p['pack_unit'].upper()}"


def lone_pine_xlsx(products: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Price List"
    ws.append(["Lone Pine Food Supply"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Customer price list - effective November 2026"])
    ws.append([])
    ws.append(["Item #", "Description", "Pack/Size", "U/M", "Case Price", "Status"])
    section = None
    for p in products:
        prefix = p["code"].split("-")[0]
        if prefix != section:
            section = prefix
            ws.append([SECTIONS[prefix]])
        ws.append([p["code"], p["name"], pack_text(p), UM.get(p["unit"], p["unit"].upper()),
                   f"${float(p['list_price']):,.2f}", "Active" if p["active"] == "yes" else "Disc"])
    ws.append([])
    ws.append(["Prices subject to change without notice."])
    wb.save(OUT / "lone_pine_price_list.xlsx")


def vendor_csv(products: list[dict]) -> None:
    rows = []
    for i, p in enumerate(products[:40]):
        rows.append({"SKU": "" if i == NO_CODE_INDEX else p["code"], "Product Name": p["name"],
                     "Sell Unit": p["unit"], "Case Pack": p["pack_size"],
                     "Price": "" if i == NO_PRICE_INDEX else p["list_price"],
                     "Active": "Y" if p["active"] == "yes" else "N"})
    dup = dict(rows[DUPLICATE_INDEX], Price="45.00")
    rows.append(dup)
    with (OUT / "vendor_export.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def simple_xlsx(products: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Product", "Price"])
    for p in [p for p in products if p["code"].startswith("PRO")] + products[60:71]:
        ws.append([p["name"], float(p["list_price"])])
    wb.save(OUT / "simple_list_no_codes.xlsx")


def print_pdf(products: list[dict]) -> None:
    chosen = [p for p in products if p["code"].startswith(("DAI", "MEA"))][:20] + \
             [p for p in products if p["code"].startswith("CAN")][4:14]
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(OUT / "price_list_print.pdf"), pagesize=letter)
    data = [["Item", "Description", "Pack", "Price"]]
    for p in chosen:
        name = p["name"] + ("  (DISCONTINUED)" if p["active"] == "no" else "")
        data.append([p["code"], name, pack_text(p), f"${float(p['list_price']):,.2f}"])
    table = Table(data, colWidths=[60, 300, 70, 70])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
    ]))
    doc.build([Paragraph("Lone Pine Food Supply - Dairy, Meat and Canned Goods", styles["Title"]),
               Paragraph("Prices per case, FOB Houston. Effective November 2026.", styles["Normal"]),
               Spacer(1, 12), table])
    reading = {"items": [{
        "item_code": p["code"], "description": p["name"], "unit": None, "pack": pack_text(p),
        "price": float(p["list_price"]), "discontinued": p["active"] == "no",
    } for p in chosen], "notes": None}
    (READINGS / "price_list_print.pdf.json").write_text(json.dumps(reading, indent=2), encoding="utf-8")


def customers_xlsx(customers: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Cust #", "Customer Name", "Email", "Ship To Address"])
    for c in customers:
        ws.append([c["account"], c["name"], c["email"], c["address"]])
    wb.save(OUT / "customer_list.xlsx")


def main() -> None:
    for folder in (OUT, READINGS):
        folder.mkdir(parents=True, exist_ok=True)
    products = read_csv(MASTER / "products.csv")
    lone_pine_xlsx(products)
    vendor_csv(products)
    simple_xlsx(products)
    print_pdf(products)
    customers_xlsx(read_csv(MASTER / "customers.csv"))
    print(f"Price lists written to {OUT}")


if __name__ == "__main__":
    main()
