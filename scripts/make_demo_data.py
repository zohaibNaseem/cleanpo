"""Make the synthetic demo data for CleanPO.

All companies, people, e-mail addresses and prices in this script are made up.
The e-mail domains use the reserved ".example" top-level domain.

Output (in demo_data/):
  master/products.csv, customers.csv, prices.csv, aliases.csv,
  master/customer_item_codes.csv, order_history.csv
  orders/      16 order files in mixed formats (PDF, e-mail, Excel, CSV, image)
  readings/    one JSON file per order: what a correct reader returns (replay mode)
  expected.json  the correct result for each order (used by the tests)

Run:  python scripts/make_demo_data.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1] / "demo_data"
MASTER = ROOT / "master"
ORDERS = ROOT / "orders"
READINGS = ROOT / "readings"

DISTRIBUTOR = "Lone Pine Food Supply"
TODAY = date(2026, 11, 2)
rng = random.Random(42)

# ---------------------------------------------------------------------------
# Products: (code prefix, name, sell unit, pack size, pack unit, list price, active)
# ---------------------------------------------------------------------------
BASE_PRODUCTS: list[tuple[str, str, str, int, str, float, bool]] = [
    # Canned goods
    ("CAN", "Tomato Paste #10 Can", "case", 6, "can", 42.50, True),
    ("CAN", "Crushed Tomatoes #10 Can", "case", 6, "can", 36.90, True),
    ("CAN", "Diced Tomatoes #10 Can", "case", 6, "can", 35.40, True),
    ("CAN", "Tomato Sauce #10 Can", "case", 6, "can", 33.80, True),
    ("CAN", "Pizza Sauce Fully Prepared #10 Can", "case", 6, "can", 44.20, True),
    ("CAN", "Black Olives Sliced #10 Can", "case", 6, "can", 58.75, True),
    ("CAN", "Chickpeas Garbanzo #10 Can", "case", 6, "can", 31.60, True),
    ("CAN", "Kidney Beans Dark Red #10 Can", "case", 6, "can", 30.95, True),
    ("CAN", "Corn Whole Kernel #10 Can", "case", 6, "can", 29.40, True),
    ("CAN", "Pineapple Tidbits #10 Can", "case", 6, "can", 39.80, True),
    ("CAN", "Peaches Sliced Light Syrup #10 Can", "case", 6, "can", 41.30, True),
    ("CAN", "Mushrooms Stems and Pieces #10 Can", "case", 6, "can", 47.60, True),
    ("CAN", "Jalapeno Slices #10 Can", "case", 6, "can", 34.10, True),
    ("CAN", "Artichoke Hearts Quartered #10 Can", "case", 6, "can", 72.40, False),
    # Oils and vinegar
    ("OIL", "Olive Oil Pure 5L Jug", "case", 3, "jug", 96.00, True),
    ("OIL", "Olive Oil Extra Virgin 5L Jug", "case", 3, "jug", 124.50, True),
    ("OIL", "Canola Oil 35lb Jug", "each", 1, "jug", 41.75, True),
    ("OIL", "Fryer Oil Clear Liquid 35lb Jug", "each", 1, "jug", 44.90, True),
    ("OIL", "Vegetable Oil 1 Gallon", "case", 6, "gallon", 52.30, True),
    ("OIL", "Red Wine Vinegar 1 Gallon", "case", 4, "gallon", 28.60, True),
    ("OIL", "Balsamic Vinegar 1 Gallon", "case", 2, "gallon", 46.80, True),
    # Dairy
    ("DAI", "Mozzarella Shredded Whole Milk 5lb Bag", "case", 4, "bag", 68.40, True),
    ("DAI", "Mozzarella Loaf Whole Milk 6lb", "case", 8, "loaf", 132.00, True),
    ("DAI", "Parmesan Grated 5lb Bag", "case", 2, "bag", 61.90, True),
    ("DAI", "Cheddar Shredded Mild 5lb Bag", "case", 4, "bag", 59.80, True),
    ("DAI", "Provolone Sliced 5lb", "case", 4, "pack", 74.30, True),
    ("DAI", "Ricotta Whole Milk 3lb Tub", "case", 4, "tub", 27.90, True),
    ("DAI", "Butter Unsalted 1lb Solid", "case", 36, "lb", 118.80, True),
    ("DAI", "Butter Salted 1lb Solid", "case", 36, "lb", 116.40, True),
    ("DAI", "Heavy Cream 40% Half Gallon", "case", 6, "carton", 49.20, True),
    ("DAI", "Whole Milk 1 Gallon", "case", 4, "gallon", 19.60, True),
    ("DAI", "Cream Cheese 3lb Loaf", "case", 10, "loaf", 79.50, True),
    ("DAI", "Eggs Large Grade AA 15 Dozen", "case", 1, "case", 38.70, True),
    ("DAI", "Sour Cream 5lb Tub", "case", 2, "tub", 18.40, True),
    # Dry goods
    ("DRY", "Flour All Purpose 50lb Bag", "each", 1, "bag", 21.40, True),
    ("DRY", "Flour High Gluten 50lb Bag", "each", 1, "bag", 24.90, True),
    ("DRY", "Sugar Granulated 50lb Bag", "each", 1, "bag", 38.60, True),
    ("DRY", "Rice Long Grain 50lb Bag", "each", 1, "bag", 33.20, True),
    ("DRY", "Spaghetti 20lb Case", "case", 1, "case", 22.80, True),
    ("DRY", "Penne Rigate 20lb Case", "case", 1, "case", 23.40, True),
    ("DRY", "Lasagna Sheets 10lb Case", "case", 1, "case", 19.90, True),
    ("DRY", "Breadcrumbs Plain 25lb Bag", "each", 1, "bag", 27.60, True),
    ("DRY", "Panko Breadcrumbs 25lb Bag", "each", 1, "bag", 41.20, True),
    ("DRY", "Yeast Instant Dry 1lb", "case", 20, "pack", 64.00, True),
    ("DRY", "Cornmeal Yellow 25lb Bag", "each", 1, "bag", 18.30, True),
    ("DRY", "Salt Kosher 3lb Box", "case", 12, "box", 26.40, True),
    ("DRY", "Black Pepper Ground 18oz", "case", 6, "jar", 71.40, True),
    ("DRY", "Oregano Leaf 5oz", "case", 6, "jar", 32.90, True),
    ("DRY", "Garlic Powder 21oz", "case", 6, "jar", 45.60, True),
    ("DRY", "Red Pepper Crushed 12oz", "case", 6, "jar", 38.20, True),
    # Meat and frozen
    ("MEA", "Pepperoni Sliced 25lb Case", "case", 1, "case", 112.50, True),
    ("MEA", "Italian Sausage Crumbles Cooked 10lb", "case", 2, "bag", 69.80, True),
    ("MEA", "Chicken Breast IQF 40lb Case", "case", 1, "case", 98.40, True),
    ("MEA", "Chicken Wings Jumbo 40lb Case", "case", 1, "case", 136.00, True),
    ("MEA", "Ground Beef 80/20 10lb Chub", "case", 4, "chub", 172.40, True),
    ("MEA", "Bacon Sliced 15lb Case", "case", 1, "case", 84.60, True),
    ("MEA", "Ham Sliced Deli 6lb", "case", 2, "pack", 52.20, True),
    ("MEA", "Meatballs Cooked 1oz 10lb", "case", 1, "case", 46.30, True),
    ("FRZ", "French Fries 3/8 Cut 30lb Case", "case", 6, "bag", 39.90, True),
    ("FRZ", "Sweet Potato Fries 30lb Case", "case", 6, "bag", 58.20, True),
    ("FRZ", "Mixed Vegetables 20lb Case", "case", 1, "case", 31.50, True),
    ("FRZ", "Spinach Chopped 12lb Case", "case", 4, "bag", 28.80, True),
    ("FRZ", "Pizza Dough Ball 16oz", "case", 40, "ball", 36.00, True),
    # Produce
    ("PRO", "Romaine Hearts 12ct", "case", 1, "case", 34.50, True),
    ("PRO", "Iceberg Lettuce 24ct", "case", 1, "case", 29.80, True),
    ("PRO", "Tomatoes Roma 25lb", "case", 1, "case", 32.60, True),
    ("PRO", "Onions Yellow Jumbo 50lb", "each", 1, "bag", 27.40, True),
    ("PRO", "Potatoes Russet 50lb", "each", 1, "bag", 24.20, True),
    ("PRO", "Garlic Peeled 5lb Jar", "case", 4, "jar", 38.90, True),
    ("PRO", "Lemons 115ct", "case", 1, "case", 44.70, True),
    ("PRO", "Basil Fresh 1lb", "each", 1, "pack", 14.60, True),
    ("PRO", "Green Peppers Large 25lb", "case", 1, "case", 36.20, True),
    # Beverages
    ("BEV", "Cola Syrup Bag in Box 5 Gallon", "each", 1, "box", 108.00, True),
    ("BEV", "Bottled Water 16.9oz 24pk", "case", 24, "bottle", 6.90, True),
    ("BEV", "Iced Tea Unsweetened Brew Bags", "case", 24, "bag", 31.40, True),
    ("BEV", "Coffee Ground Medium Roast 2.5oz Pack", "case", 42, "pack", 48.60, True),
    ("BEV", "Orange Juice 100% 64oz", "case", 8, "bottle", 34.80, True),
]

# Size variants make the list realistic and test that the size decides the match.
VARIANTS: list[tuple[str, str, list[str], str, int, str, list[float]]] = [
    ("PAP", "Pizza Box Kraft {}", ["10in", "12in", "14in", "16in", "18in"], "bundle", 50, "box",
     [18.40, 21.60, 24.80, 28.90, 33.50]),
    ("PAP", "To-Go Container Hinged Foam-Free {}", ["6x6", "8x8", "9x9", "9x6"], "case", 200, "container",
     [42.00, 49.50, 54.80, 47.20]),
    ("PAP", "Deli Container with Lid {}", ["8oz", "16oz", "32oz"], "case", 240, "container",
     [36.40, 41.80, 52.60]),
    ("PAP", "Hot Cup Paper {}", ["8oz", "12oz", "16oz", "20oz"], "case", 1000, "cup",
     [48.20, 56.90, 63.40, 71.80]),
    ("PAP", "Cold Cup Plastic {}", ["12oz", "16oz", "24oz", "32oz"], "case", 1000, "cup",
     [61.30, 68.70, 79.90, 92.40]),
    ("PAP", "Cup Lid Flat {}", ["12-24oz", "32oz"], "case", 1000, "lid", [29.80, 34.60]),
    ("PAP", "Gloves Nitrile Powder-Free {}", ["Small", "Medium", "Large", "X-Large"], "case", 1000,
     "glove", [54.00, 54.00, 54.00, 57.20]),
    ("PAP", "Gloves Vinyl Powder-Free {}", ["Medium", "Large", "X-Large"], "case", 1000, "glove",
     [31.80, 31.80, 33.40]),
    ("PAP", "Napkins Dinner White {}", ["1-ply", "2-ply"], "case", 3000, "napkin", [38.50, 52.90]),
    ("PAP", "Trash Liners {}", ["33gal", "45gal", "56gal"], "case", 100, "liner", [26.40, 31.20, 38.70]),
    ("PAP", "Aluminum Foil Roll {}", ["12in x 500ft", "18in x 500ft"], "each", 1, "roll", [24.60, 36.80]),
    ("PAP", "Plastic Wrap Roll {}", ["12in x 2000ft", "18in x 2000ft"], "each", 1, "roll", [19.80, 26.50]),
    ("PAP", "Cutlery Kit Wrapped {}", ["Fork Knife Napkin", "Fork Knife Spoon Napkin"], "case", 250,
     "kit", [27.90, 31.40]),
    ("PAP", "Straws Paper Wrapped {}", ["7.75in", "10.25in"], "case", 3000, "straw", [44.20, 49.80]),
    ("PAP", "Portion Cup with Lid {}", ["2oz", "3.25oz", "4oz"], "case", 2500, "cup", [43.60, 51.20, 57.40]),
    ("CHM", "Dish Detergent Liquid {}", ["1 Gallon", "5 Gallon"], "each", 1, "jug", [18.90, 74.50]),
    ("CHM", "Sanitizer Quat Concentrate {}", ["1 Gallon", "2.5 Gallon"], "each", 1, "jug", [21.40, 46.20]),
    ("CHM", "Degreaser Heavy Duty {}", ["1 Gallon", "5 Gallon"], "each", 1, "jug", [16.80, 66.90]),
    ("CHM", "Hand Soap Foam Refill {}", ["1000ml", "1200ml"], "case", 6, "refill", [58.40, 64.80]),
    ("CHM", "Floor Cleaner Neutral {}", ["1 Gallon", "2 Gallon"], "case", 4, "jug", [39.60, 61.20]),
    ("CHM", "Paper Towel Roll Hardwound {}", ["8in x 600ft", "8in x 800ft"], "case", 6, "roll",
     [42.80, 51.40]),
    ("DRY", "Rice Basmati {}", ["10lb", "20lb", "40lb"], "each", 1, "bag", [21.80, 39.60, 74.20]),
    ("DRY", "Sugar Powdered {}", ["2lb", "25lb", "50lb"], "each", 1, "bag", [3.20, 26.40, 48.90]),
    ("DRY", "Honey Pure {}", ["5lb Jug", "40lb Pail"], "each", 1, "jug", [24.60, 158.00]),
    ("DRY", "Ketchup Fancy {}", ["#10 Can", "1.14oz Packet"], "case", 6, "can", [29.80, 0.0]),
    ("DRY", "Mayonnaise Extra Heavy {}", ["1 Gallon", "12gm Packet"], "case", 4, "jar", [54.40, 0.0]),
    ("DRY", "Mustard Yellow {}", ["1 Gallon", "5.5gm Packet"], "case", 4, "jar", [24.80, 0.0]),
    ("DRY", "Ranch Dressing {}", ["1 Gallon", "1.5oz Cup"], "case", 4, "jar", [46.20, 0.0]),
    ("DRY", "Hot Sauce Louisiana Style {}", ["1 Gallon", "6oz Bottle"], "case", 4, "jar", [31.60, 0.0]),
]

PACKET_PACKS = {"1.14oz Packet": (500, 21.40), "12gm Packet": (500, 26.80), "5.5gm Packet": (500, 18.30),
                "1.5oz Cup": (60, 32.70), "6oz Bottle": (24, 38.40)}


def build_products() -> list[dict]:
    products: list[dict] = []
    counters: dict[str, int] = {}

    def add(prefix, name, unit, pack, pack_unit, price, active=True):
        counters[prefix] = counters.get(prefix, 100) + 10
        products.append({
            "code": f"{prefix}-{counters[prefix]}",
            "name": name,
            "unit": unit,
            "pack_size": pack,
            "pack_unit": pack_unit,
            "list_price": f"{price:.2f}",
            "active": "yes" if active else "no",
        })

    for prefix, name, unit, pack, pack_unit, price, active in BASE_PRODUCTS:
        add(prefix, name, unit, pack, pack_unit, price, active)
    for prefix, template, sizes, unit, pack, pack_unit, prices in VARIANTS:
        for size, price in zip(sizes, prices):
            if size in PACKET_PACKS:
                p_pack, p_price = PACKET_PACKS[size]
                add(prefix, template.format(size), "case", p_pack, "packet", p_price)
            else:
                add(prefix, template.format(size), unit, pack, pack_unit, price)
    return products


CUSTOMERS = [
    # account, name, email, address, delivery days, credit hold
    ("C-1001", "Tony's Pizza Kitchen", "orders@tonyspizza.example", "1420 Main St, Houston TX", "Tue,Fri", "no"),
    ("C-1002", "Bella Napoli Trattoria", "purchasing@bellanapoli.example", "88 Westheimer Rd, Houston TX", "Mon,Thu", "no"),
    ("C-1003", "Green Leaf Cafe", "sam@greenleafcafe.example", "301 Bagby St, Houston TX", "Mon,Wed,Fri", "no"),
    ("C-1004", "Rosa's Deli and Market", "buyer@rosasdeli.example", "915 Telephone Rd, Houston TX", "Tue,Fri", "no"),
    ("C-1005", "Harbor Grill", "kitchen@harborgrill.example", "22 Seawall Blvd, Galveston TX", "Wed,Sat", "no"),
    ("C-1006", "Main Street Diner", "mainstreetdiner@mail.example", "407 Main St, Pasadena TX", "Mon,Thu", "no"),
    ("C-1007", "Sunrise Bakery", "orders@sunrisebakery.example", "1190 Shepherd Dr, Houston TX", "Mon,Wed,Fri", "no"),
    ("C-1008", "Gulf Breeze Seafood House", "chef@gulfbreeze.example", "6 Pier Rd, Kemah TX", "Tue,Fri", "no"),
    ("C-1009", "Midtown Burger Co", "ops@midtownburger.example", "2700 Travis St, Houston TX", "Mon,Thu", "yes"),
    ("C-1010", "Little Saigon Pho", "order@littlesaigonpho.example", "11210 Bellaire Blvd, Houston TX", "Tue,Sat", "no"),
    ("C-1011", "Taqueria El Sol", "elsol.orders@mail.example", "5402 Airline Dr, Houston TX", "Mon,Wed,Fri", "no"),
    ("C-1012", "Heights Coffee Bar", "manager@heightscoffee.example", "19th St and Yale, Houston TX", "Tue,Fri", "no"),
    ("C-1013", "Bayou City BBQ", "pitmaster@bayoucitybbq.example", "3300 Navigation Blvd, Houston TX", "Wed,Sat", "no"),
    ("C-1014", "Ocean Star Sushi", "orders@oceanstarsushi.example", "2416 Kirby Dr, Houston TX", "Tue,Fri", "no"),
    ("C-1015", "Campus Corner Pizza", "campuscorner@mail.example", "4800 Calhoun Rd, Houston TX", "Mon,Thu", "no"),
    ("C-1016", "Sugar Land Family Diner", "sldiner@mail.example", "77 Town Center Blvd, Sugar Land TX", "Mon,Thu", "no"),
    ("C-1017", "Riverside Senior Living Kitchen", "foodservice@riversidesl.example", "500 Riverside Dr, Houston TX", "Mon,Wed,Fri", "no"),
    ("C-1018", "Northside Elementary Cafeteria", "cafeteria@northside-isd.example", "1200 Irvington Blvd, Houston TX", "Mon,Thu", "no"),
    ("C-1019", "Copper Kettle Catering", "events@copperkettle.example", "8100 Hillcroft St, Houston TX", "Tue,Fri", "no"),
    ("C-1020", "Westchase Sports Bar", "bar@westchasesports.example", "10500 Westheimer Rd, Houston TX", "Wed,Sat", "no"),
]


def p(products: list[dict], name: str) -> dict:
    """Return the product with this exact name."""
    for row in products:
        if row["name"] == name:
            return row
    raise KeyError(name)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Order documents
# ---------------------------------------------------------------------------

def pdf_order(path: Path, buyer: str, buyer_address: str, po: str, order_date: date,
              delivery: date | None, lines: list[tuple[str, str, float, str, float | None]],
              note: str = "") -> None:
    """Write a purchase order PDF. Each line: (their code, description, qty, unit, unit price)."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=letter, title=f"Purchase Order {po}")
    story = [
        Paragraph(f"<b>{buyer}</b>", styles["Title"]),
        Paragraph(buyer_address, styles["Normal"]),
        Spacer(1, 12),
        Paragraph(f"<b>PURCHASE ORDER</b> &nbsp; No. {po}", styles["Heading2"]),
        Paragraph(f"Date: {order_date:%m/%d/%Y}", styles["Normal"]),
        Paragraph(f"Vendor: {DISTRIBUTOR}", styles["Normal"]),
    ]
    if delivery:
        story.append(Paragraph(f"Requested delivery: {delivery:%A %m/%d/%Y}", styles["Normal"]))
    story.append(Spacer(1, 12))
    data = [["Item #", "Description", "Qty", "Unit", "Unit Price", "Amount"]]
    total = 0.0
    for code, desc, qty, unit, price in lines:
        amount = qty * price if price is not None else None
        total += amount or 0
        data.append([code, desc, f"{qty:g}", unit,
                     f"${price:,.2f}" if price is not None else "",
                     f"${amount:,.2f}" if amount is not None else ""])
    data.append(["", "", "", "", "Total", f"${total:,.2f}"])
    table = Table(data, colWidths=[60, 220, 40, 50, 70, 70])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDDDDD")),
        ("GRID", (0, 0), (-1, -2), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(table)
    if note:
        story += [Spacer(1, 12), Paragraph(note, styles["Normal"])]
    doc.build(story)


def eml_order(path: Path, sender_name: str, sender: str, subject: str, body: str, sent: date) -> None:
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = "orders@lonepinefood.example"
    msg["Subject"] = subject
    msg["Date"] = f"{sent:%a, %d %b %Y} 07:42:00 -0600"
    msg.set_content(body)
    path.write_bytes(bytes(msg))


def scan_order(path: Path, lines_of_text: list[str]) -> None:
    """Write an image that looks like a photo of a hand-filled order form."""
    width, height = 1240, 1400
    img = Image.new("RGB", (width, height), (246, 243, 232))
    draw = ImageDraw.Draw(img)
    hand = ImageFont.truetype("C:/Windows/Fonts/segoepr.ttf", 40)
    print_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    draw.text((60, 50), "ORDER FORM", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48), fill=(40, 40, 40))
    y = 150
    for i, text in enumerate(lines_of_text):
        if i < 3:
            draw.text((60, y), text, font=print_font, fill=(30, 30, 30))
        else:
            draw.line((60, y + 52, width - 60, y + 52), fill=(170, 170, 200), width=2)
            draw.text((80 + rng.randint(-6, 6), y), text, font=hand, fill=(25, 35, 110))
        y += 80
    img = img.rotate(1.6, expand=True, fillcolor=(90, 90, 90))
    noise = Image.effect_noise(img.size, 18).convert("RGB")
    img = Image.blend(img, noise, 0.08).filter(ImageFilter.GaussianBlur(0.6))
    img.save(path, quality=85)


def main() -> None:
    for folder in (MASTER, ORDERS, READINGS):
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():
            old.unlink()

    products = build_products()
    write_csv(MASTER / "products.csv", products)

    customers = [
        {"account": a, "name": n, "email": e, "address": ad, "delivery_days": d, "credit_hold": h}
        for a, n, e, ad, d, h in CUSTOMERS
    ]
    write_csv(MASTER / "customers.csv", customers)

    # Price list: standard prices plus special prices for two large customers.
    prices = [{"product_code": r["code"], "customer_account": "", "price": r["list_price"]} for r in products]
    special = [("C-1001", "Mozzarella Shredded Whole Milk 5lb Bag", 64.90),
               ("C-1001", "Pepperoni Sliced 25lb Case", 106.00),
               ("C-1017", "Whole Milk 1 Gallon", 18.20)]
    for account, name, price in special:
        prices.append({"product_code": p(products, name)["code"], "customer_account": account, "price": f"{price:.2f}"})
    write_csv(MASTER / "prices.csv", prices)

    # Memory list: what customers wrote before and what it meant.
    aliases = [
        {"customer_account": "C-1001", "written_as": "tomato paste", "product_code": p(products, "Tomato Paste #10 Can")["code"]},
        {"customer_account": "C-1001", "written_as": "mozz 5lb", "product_code": p(products, "Mozzarella Shredded Whole Milk 5lb Bag")["code"]},
        {"customer_account": "C-1001", "written_as": "pepperoni", "product_code": p(products, "Pepperoni Sliced 25lb Case")["code"]},
        {"customer_account": "C-1001", "written_as": "dough balls", "product_code": p(products, "Pizza Dough Ball 16oz")["code"]},
        {"customer_account": "", "written_as": "ap flour", "product_code": p(products, "Flour All Purpose 50lb Bag")["code"]},
    ]
    write_csv(MASTER / "aliases.csv", aliases)

    # Rosa's Deli uses its own item numbers.
    rosa_codes = [("RD-0042", "Ham Sliced Deli 6lb"), ("RD-0043", "Provolone Sliced 5lb"),
                  ("RD-0107", "Cream Cheese 3lb Loaf"), ("RD-0210", "Deli Container with Lid 16oz"),
                  ("RD-0211", "Deli Container with Lid 32oz"), ("RD-0300", "Gloves Vinyl Powder-Free Large")]
    write_csv(MASTER / "customer_item_codes.csv", [
        {"customer_account": "C-1004", "customer_code": c, "product_code": p(products, n)["code"]} for c, n in rosa_codes
    ])

    # Order history: 12 weeks of normal orders, used for the "unusual quantity" check.
    regulars = {
        "C-1001": [("Tomato Paste #10 Can", 10), ("Mozzarella Shredded Whole Milk 5lb Bag", 6),
                   ("Pepperoni Sliced 25lb Case", 2), ("Flour High Gluten 50lb Bag", 8), ("Pizza Box Kraft 16in", 6)],
        "C-1002": [("Crushed Tomatoes #10 Can", 4), ("Olive Oil Extra Virgin 5L Jug", 1), ("Penne Rigate 20lb Case", 2),
                   ("Parmesan Grated 5lb Bag", 2), ("Tomato Paste #10 Can", 2)],
        "C-1003": [("Heavy Cream 40% Half Gallon", 3), ("Butter Unsalted 1lb Solid", 2), ("Flour All Purpose 50lb Bag", 2)],
        "C-1004": [("Ham Sliced Deli 6lb", 3), ("Provolone Sliced 5lb", 2), ("Deli Container with Lid 16oz", 1)],
        "C-1005": [("French Fries 3/8 Cut 30lb Case", 5), ("Chicken Wings Jumbo 40lb Case", 2), ("Fryer Oil Clear Liquid 35lb Jug", 3)],
        "C-1006": [("Eggs Large Grade AA 15 Dozen", 3), ("Bacon Sliced 15lb Case", 2), ("Potatoes Russet 50lb", 2)],
        "C-1007": [("Flour All Purpose 50lb Bag", 10), ("Sugar Granulated 50lb Bag", 4), ("Butter Unsalted 1lb Solid", 3),
                   ("Eggs Large Grade AA 15 Dozen", 4), ("Yeast Instant Dry 1lb", 1)],
        "C-1009": [("Ground Beef 80/20 10lb Chub", 4), ("French Fries 3/8 Cut 30lb Case", 4)],
        "C-1015": [("Pizza Sauce Fully Prepared #10 Can", 3), ("Mozzarella Shredded Whole Milk 5lb Bag", 4)],
    }
    history = []
    for account, items in regulars.items():
        for week in range(12, 0, -1):
            day = TODAY - timedelta(weeks=week)
            for name, qty in items:
                history.append({"customer_account": account, "order_date": day.isoformat(),
                                "product_code": p(products, name)["code"],
                                "qty": max(1, qty + rng.choice([-1, 0, 0, 1]))})
    write_csv(MASTER / "order_history.csv", history)

    orders: list[dict] = []

    def record(file_name: str, reading: dict, expect_status: str, expect_codes: list[str], story: str) -> None:
        (READINGS / f"{file_name}.json").write_text(json.dumps(reading, indent=2), encoding="utf-8")
        orders.append({"file": file_name, "status": expect_status, "issue_codes": sorted(expect_codes), "story": story})

    def line(raw, desc, qty, unit=None, price=None, code=None):
        return {"raw_text": raw, "item_code": code, "description": desc, "quantity": qty,
                "unit": unit, "unit_price": price}

    def reading(name, email, po, odate, ddate, lines, ship_to=None, is_order=True, notes=None):
        return {"is_order": is_order, "customer_name": name, "customer_email": email, "po_number": po,
                "order_date": odate, "requested_delivery_date": ddate, "ship_to": ship_to,
                "lines": lines, "notes": notes}

    tony = CUSTOMERS[0]
    d_tue = TODAY + timedelta(days=1)
    d_fri = TODAY + timedelta(days=4)

    # O01: clean PDF with the distributor's own item codes.
    tp, mz, pep, hg, box16 = (p(products, n) for n in ["Tomato Paste #10 Can", "Mozzarella Shredded Whole Milk 5lb Bag",
                                                       "Pepperoni Sliced 25lb Case", "Flour High Gluten 50lb Bag",
                                                       "Pizza Box Kraft 16in"])
    o1 = [(tp["code"], tp["name"], 10, "case", 42.50), (mz["code"], mz["name"], 6, "case", 64.90),
          (pep["code"], pep["name"], 2, "case", 106.00), (hg["code"], hg["name"], 8, "bag", 24.90),
          (box16["code"], box16["name"], 6, "bundle", 28.90)]
    pdf_order(ORDERS / "01_tonys_po_1001.pdf", tony[1], tony[3], "TPK-1001", TODAY, d_fri, o1)
    record("01_tonys_po_1001.pdf",
           reading(tony[1], None, "TPK-1001", TODAY.isoformat(), d_fri.isoformat(),
                   [line(f"{c} {d} {q} {u} {pr}", d, q, u, pr, c) for c, d, q, u, pr in o1], ship_to=tony[3]),
           "ready", [], "Clean PDF purchase order with our item codes.")

    # O02: PDF with descriptions only, written the customer's way.
    bella = CUSTOMERS[1]
    o2 = [("", "Crushed tomatoes #10", 4, "cs", 36.90), ("", "Extra virgin olive oil 5 liter", 1, "cs", 124.50),
          ("", "Penne rigate 20 lb", 2, "cs", 23.40), ("", "Grated parmesan 5lb", 2, "cs", 61.90)]
    pdf_order(ORDERS / "02_bella_napoli_po.pdf", bella[1], bella[3], "BN-5531", TODAY, TODAY + timedelta(days=3), o2)
    record("02_bella_napoli_po.pdf",
           reading(bella[1], None, "BN-5531", TODAY.isoformat(), (TODAY + timedelta(days=3)).isoformat(),
                   [line(f"{d} {q} {u} {pr}", d, q, u, pr) for _, d, q, u, pr in o2], ship_to=bella[3]),
           "ready", [], "PDF with descriptions only, no item codes.")

    # O03: plain-language e-mail.
    green = CUSTOMERS[2]
    body3 = ("Hi team,\n\nCan you please send for Wednesday:\n"
             "- 3 cases heavy cream\n- 2 bags AP flour (50lb)\n- 2 cases unsalted butter\n\nThanks!\nSam\nGreen Leaf Cafe")
    eml_order(ORDERS / "03_green_leaf_email.eml", "Sam Ortiz", green[2], "Order for Wed", body3, TODAY)
    record("03_green_leaf_email.eml",
           reading("Green Leaf Cafe", green[2], None, TODAY.isoformat(), None,
                   [line("3 cases heavy cream", "heavy cream", 3, "case"),
                    line("2 bags AP flour (50lb)", "AP flour 50lb", 2, "bag"),
                    line("2 cases unsalted butter", "unsalted butter", 2, "case")]),
           "ready", [], "Short e-mail in plain words.")

    # O04: e-mail with shorthand that only the memory list can resolve.
    body4 = "need for friday: 10 cs tomato paste, 6 mozz 5lb, 2 pepperoni, 8 high gluten flour. thx -Tony"
    eml_order(ORDERS / "04_tonys_shorthand_email.eml", "Tony Russo", tony[2], "order", body4, TODAY)
    record("04_tonys_shorthand_email.eml",
           reading(None, tony[2], None, TODAY.isoformat(), None,
                   [line("10 cs tomato paste", "tomato paste", 10, "cs"), line("6 mozz 5lb", "mozz 5lb", 6, None),
                    line("2 pepperoni", "pepperoni", 2, None), line("8 high gluten flour", "high gluten flour", 8, None)]),
           "ready", [], "Shorthand e-mail; the memory list knows what Tony means.")

    # O05: Excel order with the customer's own item codes.
    rosa = CUSTOMERS[3]
    wb = Workbook()
    ws = wb.active
    ws.title = "Order"
    ws.append(["Rosa's Deli and Market - weekly order"])
    ws.append(["Date", TODAY.isoformat(), "", "PO", "RD-2026-118"])
    ws.append([])
    ws.append(["Our Item No", "Item", "Qty", "UOM"])
    o5 = [("RD-0042", "Deli ham sliced", 3, "CS"), ("RD-0043", "Provolone sliced", 2, "CS"),
          ("RD-0107", "Cream cheese loaves", 1, "CS"), ("RD-0210", "16oz deli cups w/ lids", 2, "CS")]
    for row in o5:
        ws.append(list(row))
    wb.save(ORDERS / "05_rosas_deli_order.xlsx")
    record("05_rosas_deli_order.xlsx",
           reading("Rosa's Deli and Market", None, "RD-2026-118", TODAY.isoformat(), None,
                   [line(f"{c} {d} {q} {u}", d, q, u, None, c) for c, d, q, u in o5]),
           "ready", [], "Excel sheet with the customer's own item numbers.")

    # O06: CSV export from the customer's purchasing tool.
    harbor = CUSTOMERS[4]
    with (ORDERS / "06_harbor_grill_order.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["customer", "Harbor Grill"])
        w.writerow(["po", "HG-7781"])
        w.writerow(["product", "quantity", "unit"])
        o6 = [("Fries 3/8 cut 30lb", 5, "case"), ("Jumbo chicken wings 40lb", 2, "case"),
              ("Fryer oil 35lb", 3, "jug"), ("Romaine hearts", 1, "case")]
        w.writerows(o6)
    record("06_harbor_grill_order.csv",
           reading("Harbor Grill", None, "HG-7781", None, None,
                   [line(f"{d},{q},{u}", d, q, u) for d, q, u in o6]),
           "ready", [], "CSV file from the customer's purchasing tool.")

    # O07: photo of a hand-filled order form.
    diner = CUSTOMERS[5]
    scan_lines = [f"Customer: {diner[1]}", f"Phone order sheet - {TODAY:%m/%d/%Y}", "Deliver: Thursday",
                  "3  cs  eggs large", "2  cs  bacon sliced", "2  bags  russet potatoes 50#", "1  cs  romaine"]
    scan_order(ORDERS / "07_main_street_diner_scan.jpg", scan_lines)
    record("07_main_street_diner_scan.jpg",
           reading(diner[1], None, None, TODAY.isoformat(), None,
                   [line("3 cs eggs large", "eggs large", 3, "cs"), line("2 cs bacon sliced", "bacon sliced", 2, "cs"),
                    line("2 bags russet potatoes 50#", "russet potatoes 50#", 2, "bag"),
                    line("1 cs romaine", "romaine", 1, "cs")]),
           "ready", [], "Photo of a hand-written order form.")

    # O08: planted error - an old price on one line.
    o8 = [("", "Tomato paste #10", 2, "cs", 38.00), ("", "Crushed tomatoes #10", 4, "cs", 36.90),
          ("", "Grated parmesan 5lb", 2, "cs", 61.90)]
    pdf_order(ORDERS / "08_bella_napoli_old_price.pdf", bella[1], bella[3], "BN-5540", TODAY, None, o8)
    record("08_bella_napoli_old_price.pdf",
           reading(bella[1], None, "BN-5540", TODAY.isoformat(), None,
                   [line(f"{d} {q} {u} {pr}", d, q, u, pr) for _, d, q, u, pr in o8], ship_to=bella[3]),
           "needs_review", ["PRICE_MISMATCH"], "Planted error: tomato paste at $38.00, list price is $42.50.")

    # O09: planted error - the same purchase order sent again.
    pdf_order(ORDERS / "09_tonys_po_1001_resent.pdf", tony[1], tony[3], "TPK-1001", TODAY, d_fri, o1,
              note="Resending in case you did not get it.")
    record("09_tonys_po_1001_resent.pdf",
           reading(tony[1], None, "TPK-1001", TODAY.isoformat(), d_fri.isoformat(),
                   [line(f"{c} {d} {q} {u} {pr}", d, q, u, pr, c) for c, d, q, u, pr in o1], ship_to=tony[3],
                   notes="Resending in case you did not get it."),
           "needs_review", ["DUPLICATE_ORDER"], "Planted error: PO TPK-1001 sent a second time.")

    # O10: planted error - a typing mistake in the quantity.
    sun = CUSTOMERS[6]
    body10 = "Good morning,\nPlease add to Monday's delivery:\n500 bags all purpose flour 50lb\n4 bags granulated sugar\n\nMaria, Sunrise Bakery"
    eml_order(ORDERS / "10_sunrise_bakery_email.eml", "Maria Chen", sun[2], "Monday order", body10, TODAY)
    record("10_sunrise_bakery_email.eml",
           reading("Sunrise Bakery", sun[2], None, TODAY.isoformat(), None,
                   [line("500 bags all purpose flour 50lb", "all purpose flour 50lb", 500, "bag"),
                    line("4 bags granulated sugar", "granulated sugar", 4, "bag")]),
           "needs_review", ["QTY_UNUSUAL"], "Planted error: 500 bags of flour, usual order is about 10.")

    # O11: planted error - a sender who is not a customer yet.
    body11 = "Hello, we are a new restaurant opening next week. Please send 4 cases canola oil 35lb and 2 cases jumbo wings.\nKevin, Lucky Dragon Kitchen"
    eml_order(ORDERS / "11_unknown_sender_email.eml", "Kevin Wu", "kevin@luckydragon.example", "First order", body11, TODAY)
    record("11_unknown_sender_email.eml",
           reading("Lucky Dragon Kitchen", "kevin@luckydragon.example", None, TODAY.isoformat(), None,
                   [line("4 cases canola oil 35lb", "canola oil 35lb", 4, "case"),
                    line("2 cases jumbo wings", "jumbo wings", 2, "case")]),
           "needs_review", ["CUSTOMER_UNKNOWN"], "Planted error: sender is not in the customer list.")

    # O12: planted error - an item that is no longer sold.
    o12 = [("", "Artichoke hearts quartered #10", 1, "cs", 72.40), ("", "Jumbo chicken wings 40lb", 2, "cs", 136.00)]
    pdf_order(ORDERS / "12_harbor_grill_discontinued.pdf", harbor[1], harbor[3], "HG-7790", TODAY, None, o12)
    record("12_harbor_grill_discontinued.pdf",
           reading(harbor[1], None, "HG-7790", TODAY.isoformat(), None,
                   [line(f"{d} {q} {u} {pr}", d, q, u, pr) for _, d, q, u, pr in o12], ship_to=harbor[3]),
           "needs_review", ["ITEM_INACTIVE"], "Planted error: artichoke hearts are discontinued.")

    # O13: planted error - "olive oil 5L" fits two products.
    camp = CUSTOMERS[14]
    body13 = "Hi, for Thursday please: 2 olive oil 5L, 3 cs pizza sauce, 4 cs shredded mozzarella. Thanks, Campus Corner Pizza"
    eml_order(ORDERS / "13_campus_corner_email.eml", "Jordan Lee", camp[2], "Thursday order", body13, TODAY)
    record("13_campus_corner_email.eml",
           reading("Campus Corner Pizza", camp[2], None, TODAY.isoformat(), None,
                   [line("2 olive oil 5L", "olive oil 5L", 2, None), line("3 cs pizza sauce", "pizza sauce", 3, "cs"),
                    line("4 cs shredded mozzarella", "shredded mozzarella", 4, "cs")]),
           "needs_review", ["ITEM_AMBIGUOUS"], "Planted problem: 'olive oil 5L' could be Pure or Extra Virgin.")

    # O14: units - cans in place of cases; the tool converts 60 cans to 10 cases.
    elsol = CUSTOMERS[10]
    body14 = "Buenos dias! Order for Wednesday: 60 cans crushed tomatoes #10 and 2 cases jalapeno slices #10. Gracias, Taqueria El Sol"
    eml_order(ORDERS / "14_taqueria_el_sol_email.eml", "Luis Ramos", elsol[2], "Pedido / order", body14, TODAY)
    record("14_taqueria_el_sol_email.eml",
           reading("Taqueria El Sol", elsol[2], None, TODAY.isoformat(), None,
                   [line("60 cans crushed tomatoes #10", "crushed tomatoes #10", 60, "can"),
                    line("2 cases jalapeno slices #10", "jalapeno slices #10", 2, "case")]),
           "ready", ["UNIT_CONVERTED"], "Units: 60 cans become 10 cases (6 cans per case). Information only.")

    # O15: planted error - customer is on credit hold.
    mid = CUSTOMERS[8]
    o15 = [("", "Ground beef 80/20 10lb chub", 4, "cs", 172.40), ("", "French fries 3/8 cut", 4, "cs", 39.90)]
    pdf_order(ORDERS / "15_midtown_burger_po.pdf", mid[1], mid[3], "MB-0912", TODAY, None, o15)
    record("15_midtown_burger_po.pdf",
           reading(mid[1], None, "MB-0912", TODAY.isoformat(), None,
                   [line(f"{d} {q} {u} {pr}", d, q, u, pr) for _, d, q, u, pr in o15], ship_to=mid[3]),
           "needs_review", ["CREDIT_HOLD"], "Planted problem: customer account is on credit hold.")

    # O16: an e-mail that is not an order.
    body16 = "Hi, quick question on invoice 44812 - we were charged for 3 cases of lemons but only got 2. Can you check? Thanks, Sam"
    eml_order(ORDERS / "16_green_leaf_question.eml", "Sam Ortiz", green[2], "Invoice 44812 question", body16, TODAY)
    record("16_green_leaf_question.eml",
           reading("Green Leaf Cafe", green[2], None, None, None, [], is_order=False,
                   notes="Question about invoice 44812, not an order."),
           "not_order", ["NOT_AN_ORDER"], "Not an order: a billing question. Kept out of the import file.")

    (ROOT / "expected.json").write_text(json.dumps(orders, indent=2), encoding="utf-8")
    print(f"products={len(products)} customers={len(customers)} orders={len(orders)} history_rows={len(history)}")


if __name__ == "__main__":
    main()
