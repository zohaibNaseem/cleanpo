"""Match what the customer wrote to the distributor's own customer and product records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import duckdb
from rapidfuzz import fuzz, process

# Shorthand that customers use, written out in full before name matching.
SYNONYMS = {
    "ap": "all purpose", "evoo": "extra virgin olive oil", "mozz": "mozzarella", "parm": "parmesan",
    "w": "with", "lbs": "lb", "liter": "l", "litre": "l", "liters": "l", "gal": "gallon",
    "xl": "x-large", "lg": "large", "med": "medium", "sm": "small", "doz": "dozen",
}
SIZE_UNITS = {"lbs": "lb", "liters": "l", "litres": "l", "liter": "l", "litre": "l"}
# Words that carry no product meaning.
STOP_WORDS = {"cs", "case", "cases", "ctn", "carton", "please", "of", "the", "and", "a", "for"}

UNIT_WORDS = {
    "cs": "case", "case": "case", "cases": "case", "ca": "case", "ctn": "case",
    "can": "can", "cans": "can", "bag": "bag", "bags": "bag", "jug": "jug", "jugs": "jug",
    "ea": "each", "each": "each", "pc": "each", "pcs": "each", "bundle": "bundle", "bdl": "bundle",
    "box": "box", "boxes": "box", "bottle": "bottle", "bottles": "bottle", "gallon": "gallon",
    "gallons": "gallon", "gal": "gallon", "jar": "jar", "jars": "jar", "roll": "roll", "rolls": "roll",
}

# Shared mail providers: the domain of such an address does not identify the customer.
PUBLIC_MAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "msn.com",
                       "aol.com", "icloud.com", "me.com", "comcast.net", "att.net", "sbcglobal.net",
                       "verizon.net", "protonmail.com", "proton.me"}

ACCEPT_SCORE = 90     # a name match must score at least this
CLEAR_GAP = 6         # and be this much better than the next product
CANDIDATE_SCORE = 60  # below this a product is not shown as a candidate


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"(\d+)\s*#", r"\1lb", text)               # 50# -> 50lb
    text = re.sub(r"(\d+)\s*(lbs|lb|oz|liters|litres|liter|litre|l|gal|in|ct)\b",
                  lambda m: m.group(1) + SIZE_UNITS.get(m.group(2), m.group(2)), text)
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    words = []
    for word in text.split():
        word = SYNONYMS.get(word, word)
        words.extend(word.split())
    words = [SYNONYMS.get(w, w) for w in words if w not in STOP_WORDS]
    return " ".join(words).strip()


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    key = re.sub(r"[^a-z]", "", unit.lower())
    return UNIT_WORDS.get(key, key or None)


@dataclass
class Product:
    code: str
    name: str
    unit: str
    pack_size: int
    pack_unit: str
    list_price: float
    active: bool


@dataclass
class ProductMatch:
    code: Optional[str]
    method: str                 # code | customer_code | memory | name | none
    score: float = 0.0
    candidates: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return self.code is None and len(self.candidates) > 1


@dataclass
class CustomerMatch:
    account: Optional[str]
    name: Optional[str]
    method: str                 # email | domain | name | new | none
    credit_hold: bool = False


class Catalog:
    """The distributor's master lists, held in memory for fast matching."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.products: dict[str, Product] = {}
        for code, name, unit, pack, pack_unit, price, active in con.execute(
            "SELECT code, name, unit, pack_size, pack_unit, list_price, active FROM products"
        ).fetchall():
            self.products[code.strip().upper()] = Product(
                code=code.strip().upper(), name=name, unit=normalize_unit(unit) or "each",
                pack_size=int(pack or 1), pack_unit=normalize_unit(pack_unit) or "",
                list_price=float(price or 0), active=(active or "yes").strip().lower() in ("yes", "y", "true", "1"),
            )
        self._norm_names = {code: normalize(p.name) for code, p in self.products.items()}

        self.customers = con.execute(
            "SELECT account, name, lower(email), coalesce(credit_hold, 'no') FROM customers"
        ).fetchall()

        self.prices: dict[tuple[str, str], float] = {}
        for code, account, price in con.execute(
            "SELECT product_code, coalesce(customer_account, ''), price FROM prices"
        ).fetchall():
            self.prices[(code.strip().upper(), (account or "").strip())] = float(price)

        self.reload_aliases()

        self.customer_codes: dict[tuple[str, str], str] = {}
        if _table_exists(con, "customer_item_codes"):
            for account, ccode, pcode in con.execute(
                "SELECT customer_account, customer_code, product_code FROM customer_item_codes"
            ).fetchall():
                self.customer_codes[(account, ccode.strip().upper())] = pcode.strip().upper()

        self.history: dict[tuple[str, str], list[float]] = {}
        if _table_exists(con, "order_history"):
            for account, code, qty in con.execute(
                "SELECT customer_account, product_code, CAST(qty AS DOUBLE) FROM order_history"
            ).fetchall():
                self.history.setdefault((account, code.strip().upper()), []).append(qty)

    def reload_aliases(self) -> None:
        self.aliases: dict[tuple[str, str], str] = {}
        rows = []
        if _table_exists(self.con, "aliases"):
            rows += self.con.execute(
                "SELECT coalesce(customer_account, ''), written_as, product_code FROM aliases"
            ).fetchall()
        rows += self.con.execute(
            "SELECT coalesce(customer_account, ''), written_as, product_code FROM learned_aliases"
        ).fetchall()
        for account, written, code in rows:
            self.aliases[(account or "", normalize(written))] = code.strip().upper()

    # ----- Customers -------------------------------------------------------

    def match_customer(self, sender_email: Optional[str], doc_email: Optional[str],
                       name: Optional[str]) -> CustomerMatch:
        for address in (sender_email, doc_email):
            if not address:
                continue
            address = address.lower().strip()
            for account, cname, cemail, hold in self.customers:
                if cemail and address == cemail:
                    return CustomerMatch(account, cname, "email", hold.lower() == "yes")
        for address in (sender_email, doc_email):
            if not address or "@" not in address:
                continue
            domain = address.split("@", 1)[1].lower()
            if domain.startswith("mail.") or domain in PUBLIC_MAIL_DOMAINS:
                continue  # shared mail provider: the domain does not identify the customer
            hits = [c for c in self.customers if c[2] and c[2].endswith("@" + domain)]
            if len(hits) == 1:
                account, cname, _, hold = hits[0]
                return CustomerMatch(account, cname, "domain", hold.lower() == "yes")
        if name:
            names = {c[0]: normalize(c[1]) for c in self.customers}
            scored = process.extract(normalize(name), names, scorer=fuzz.token_set_ratio, limit=2)
            if scored and scored[0][1] >= ACCEPT_SCORE and (len(scored) == 1 or scored[0][1] - scored[1][1] >= CLEAR_GAP):
                account = scored[0][2]
                row = next(c for c in self.customers if c[0] == account)
                return CustomerMatch(account, row[1], "name", row[3].lower() == "yes")
        return CustomerMatch(None, None, "none")

    def add_customer(self, name: Optional[str], email: Optional[str]) -> str:
        """Add a customer that is not in the list (free test without a customer list). Return the account."""
        used = {c[0] for c in self.customers}
        n = 1
        while f"NEW-{n:03d}" in used:
            n += 1
        account = f"NEW-{n:03d}"
        email = (email or "").lower().strip() or None
        name = name or email or account
        self.con.execute("INSERT INTO customers (account, name, email, credit_hold) VALUES (?, ?, ?, 'no')",
                         [account, name, email])
        self.customers.append((account, name, email, "no"))
        return account

    # ----- Products --------------------------------------------------------

    def match_product(self, description: str, item_code: Optional[str], account: Optional[str]) -> ProductMatch:
        if item_code:
            code = item_code.strip().upper()
            if code in self.products:
                return ProductMatch(code, "code", 100)
            if account and (account, code) in self.customer_codes:
                return ProductMatch(self.customer_codes[(account, code)], "customer_code", 100)

        written = normalize(description)
        for key in ((account or "", written), ("", written)):
            if key in self.aliases and self.aliases[key] in self.products:
                return ProductMatch(self.aliases[key], "memory", 100)

        if not written:
            return ProductMatch(None, "none")
        scored = process.extract(written, self._norm_names, scorer=fuzz.token_set_ratio, limit=8)
        # Break ties between equal scores with a whole-string similarity.
        scored = sorted(
            ((code, score, fuzz.ratio(written, self._norm_names[code])) for _, score, code in scored),
            key=lambda item: (item[1], item[2]), reverse=True,
        )
        candidates = [(code, self.products[code].name, float(score))
                      for code, score, _ in scored if score >= CANDIDATE_SCORE][:4]
        if not candidates:
            return ProductMatch(None, "none")
        top_code, top_score, _ = scored[0]
        second = scored[1][1] if len(scored) > 1 else 0
        if top_score >= ACCEPT_SCORE and top_score - second >= CLEAR_GAP:
            return ProductMatch(top_code, "name", float(top_score), candidates)
        return ProductMatch(None, "none", float(top_score), candidates)

    def price_for(self, code: str, account: Optional[str]) -> float:
        if account and (code, account) in self.prices:
            return self.prices[(code, account)]
        return self.prices.get((code, ""), self.products[code].list_price if code in self.products else 0.0)

    def usual_qty(self, account: Optional[str], code: str) -> Optional[float]:
        values = self.history.get((account or "", code))
        return max(values) if values else None

    def history_names(self, account: Optional[str]) -> list[str]:
        codes = sorted({code for (acc, code) in self.history if acc == account})
        return [f"{c}: {self.products[c].name}" for c in codes if c in self.products]


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0])
