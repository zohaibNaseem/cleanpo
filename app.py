"""CleanPO web app.  Start with:  streamlit run app.py"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cleanpo import db, export, metrics
from cleanpo.matcher import Catalog
from cleanpo.pipeline import ISSUE_TEXT, approve, process_file, reject
from cleanpo.reader import PRICES_PER_MTOK, Reader, api_key_available, parse_email

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"                 # local working data, never committed
DEMO = ROOT / "demo_data"
DB_PATH = DATA / "cleanpo.duckdb"
INBOX = DATA / "inbox"

st.set_page_config(page_title="CleanPO", page_icon="📦", layout="wide")

st.markdown("""
<style>
  .block-container {padding-top: 1.4rem; max-width: 1320px;}
  header[data-testid="stHeader"] {background: transparent;}
  section[data-testid="stSidebar"] {border-right: 1px solid #232A38;}
  section[data-testid="stSidebar"] div[role="radiogroup"] label {padding: 5px 2px;}
  .cpo-brand {font-size: 1.5rem; font-weight: 800; letter-spacing: -.02em; color: #E6E9EF;}
  .cpo-brand span {color: #4C8DFF;}
  .cpo-sub {color: #8B93A7; margin-top: -2px; font-size: .88rem;}
  .cpo-note {font-size: .8rem; color: #7A8296;}
  .cpo-h1 {font-size: 1.85rem; font-weight: 700; letter-spacing: -.02em; margin: 0; color: #F2F4F8;}
  .cpo-date {color: #8B93A7; font-size: .92rem; margin-top: 2px;}
  .cpo-card {background: #161B26; border: 1px solid #232A38; border-radius: 12px; padding: 16px 18px;}
  .cpo-card .label {color: #8B93A7; font-size: .74rem; text-transform: uppercase; letter-spacing: .08em;
                    display: flex; align-items: center; gap: 8px;}
  .cpo-card .dot {display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--accent);}
  .cpo-card .value {font-size: 1.95rem; font-weight: 700; letter-spacing: -.02em; margin: 8px 0 2px; color: #F2F4F8;}
  .cpo-card .sub {color: #8B93A7; font-size: .85rem;}
  .cpo-card .up {color: #3DD68C;} .cpo-card .down {color: #FF7A7A;}
  .cpo-row {display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 9px 0;
            border-bottom: 1px solid #232A38;}
  .cpo-row:last-child {border-bottom: none;}
  .cpo-row .who {font-weight: 600; color: #E6E9EF;}
  .cpo-row .id {color: #7A8296; font-size: .82rem; margin-right: 8px; font-variant-numeric: tabular-nums;}
  .cpo-pill {display: inline-block; background: rgba(255,176,32,.10); color: #F5B547;
             border: 1px solid rgba(255,176,32,.28); border-radius: 999px; padding: 1px 9px; font-size: .74rem;
             margin-left: 4px; white-space: nowrap;}
  .cpo-ok {color: #3DD68C; padding: 8px 0;}
  .cpo-noteblock {color: #C9CFDA; line-height: 1.6; font-size: .95rem;}
  div[data-testid="stMetric"] {background: #161B26; border: 1px solid #232A38; border-radius: 12px; padding: 14px 18px;}
  div[data-testid="stMetricLabel"] p {color: #8B93A7; font-size: .78rem; text-transform: uppercase; letter-spacing: .07em;}
  div[data-testid="stMetricValue"] {font-size: 2rem; font-weight: 700;}
  h3 {font-size: 1.02rem !important; font-weight: 600 !important; color: #E6E9EF;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

@st.cache_resource
def get_con():
    con = db.connect(DB_PATH)
    if not db.has_table(con, "products"):
        db.load_master(con, DEMO / "master")
        metrics.seed_demo_history(con)
    return con


con = get_con()


def get_catalog() -> Catalog:
    if "catalog" not in st.session_state:
        st.session_state.catalog = Catalog(con)
    return st.session_state.catalog


def get_reader() -> Reader:
    model = db.get_setting(con, "model")
    key = (model, api_key_available())
    if st.session_state.get("reader_key") != key:
        st.session_state.reader = Reader(mode="auto", model=model, replay_dir=DEMO / "readings")
        st.session_state.reader_key = key
    return st.session_state.reader


def md(text) -> str:
    """Escape dollar signs, so that Streamlit markdown does not show them as math."""
    return str(text or "").replace("$", "\\$")


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="cpo-h1">{title}</div><div class="cpo-date">{subtitle}</div>', unsafe_allow_html=True)
    st.write("")


def money(value: float) -> str:
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def waiting_count() -> int:
    return con.execute("SELECT count(*) FROM orders WHERE status = 'needs_review'").fetchone()[0]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

PAGES = ["Dashboard", "Process orders", "Review", "Export", "Settings"]


def go_to(target: str) -> None:
    st.session_state.page = target


company = db.get_setting(con, "company_name")
with st.sidebar:
    st.markdown('<div class="cpo-brand">Clean<span>PO</span></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="cpo-sub">{company}</div>', unsafe_allow_html=True)
    st.write("")
    n_wait = waiting_count()
    st.session_state.setdefault("page", "Dashboard")
    page = st.radio("Go to", PAGES, key="page", label_visibility="collapsed")
    if n_wait:
        st.caption(f"⚠ {n_wait} order(s) waiting in Review")
    st.divider()
    reader = get_reader()
    if reader.uses_ai:
        st.caption(f"Reading: Claude ({reader.model})")
    else:
        st.caption("Reading: saved sample readings (no API key)")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def kpi_card(label: str, value: str, sub: str, accent: str) -> str:
    return (f'<div class="cpo-card" style="--accent:{accent}"><div class="label">{label}</div>'
            f'<div class="value">{value}</div><div class="sub">{sub}</div></div>')


def change_text(now: float, before: float) -> str:
    if not before:
        return ""
    pct = (now - before) / before * 100
    if abs(pct) < 0.5:
        return "same as previous period"
    arrow, cls = ("&#9650;", "up") if pct >= 0 else ("&#9660;", "down")
    return f'<span class="{cls}">{arrow} {abs(pct):.0f}%</span> vs previous period'


def dark_layout(fig, height: int) -> None:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), bargap=0.4,
                      font=dict(color="#AEB5C4", size=12), hoverlabel=dict(bgcolor="#1A1F2B"),
                      legend=dict(orientation="h", y=1.14, x=0, font=dict(color="#AEB5C4")),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")


def page_dashboard():
    head, period_col, cust_col = st.columns([2.6, 1.1, 1.5])
    head.markdown(f'<div class="cpo-h1">Order desk</div>'
                  f'<div class="cpo-date">{company} &middot; {datetime.now():%A %d %B %Y}</div>',
                  unsafe_allow_html=True)
    period = period_col.selectbox("Period", ["This week", "Last 4 weeks", "All time"], index=1,
                                  label_visibility="collapsed")
    customers = ["All customers"] + [r[0] for r in con.execute(
        "SELECT DISTINCT customer_name FROM orders WHERE customer_name IS NOT NULL ORDER BY 1").fetchall()]
    customer = cust_col.selectbox("Customer", customers, label_visibility="collapsed")

    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    span = {"This week": timedelta(weeks=1), "Last 4 weeks": timedelta(weeks=4), "All time": None}[period]
    start = week_start + timedelta(weeks=1) - span if span else None
    account = None
    if customer != "All customers":
        row = con.execute("SELECT customer_account FROM orders WHERE customer_name = ? LIMIT 1", [customer]).fetchone()
        account = row[0] if row else None

    k = metrics.kpis(con, start=start, account=account)
    prev = metrics.kpis(con, start=start - span, end=start, account=account) if span else None

    st.write("")
    cards = [
        ("Time saved", f"{k['hours_saved']:,.1f} h",
         change_text(k["hours_saved"], prev["hours_saved"]) if prev and prev["orders"] else "manual entry avoided",
         "#4C8DFF"),
        ("Money saved", money(k["money_saved"]), f"at ${k['hourly_rate']:.2f} per clerk hour", "#3DD68C"),
        ("Order value", money(k["order_value"]),
         f"avg {money(k['order_value'] / k['orders']) if k['orders'] else '$0'} per order", "#22C1C3"),
        ("Orders processed", f"{k['orders']:,}", f"{k['lines']:,} order lines", "#A78BFA"),
        ("Automatic", f"{k['automatic_pct']:.0f}%", f"{k['automatic']:,} of {k['orders']:,} needed no fix", "#F5B547"),
    ]
    for col, card in zip(st.columns(5), cards):
        col.markdown(kpi_card(*card), unsafe_allow_html=True)

    st.write("")
    weekly = metrics.weekly(con, account=account)
    left, right = st.columns([1.75, 1])
    with left, st.container(border=True):
        st.subheader("Hours saved per week")
        if weekly.empty:
            st.caption("No orders yet.")
        else:
            this_week = (datetime.now() - timedelta(days=datetime.now().weekday())).date()
            labels = ["This week" if w.date() == this_week else w.strftime("%d %b") for w in weekly["week"]]
            fig = go.Figure()
            fig.add_bar(x=labels, y=weekly["hours_saved"], name="Hours saved", marker_color="#4C8DFF",
                        marker_line_width=0, hovertemplate="Week of %{x}<br>%{y:.1f} hours saved<extra></extra>")
            fig.add_scatter(x=labels, y=weekly["automatic_pct"], name="Automatic %", yaxis="y2",
                            mode="lines+markers", line=dict(color="#3DD68C", width=2.5), marker=dict(size=7),
                            hovertemplate="%{y:.0f}% automatic<extra></extra>")
            dark_layout(fig, 290)
            fig.update_layout(yaxis=dict(title="Hours", gridcolor="#232A38", zeroline=False),
                              yaxis2=dict(overlaying="y", side="right", range=[0, 100], showgrid=False,
                                          ticksuffix="%"),
                              xaxis=dict(showgrid=False))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with right, st.container(border=True):
        waiting = con.execute(
            """SELECT o.id, coalesce(o.customer_name, o.sender_email, o.file_name),
                      (SELECT string_agg(DISTINCT code, ',') FROM issues i WHERE i.order_id = o.id AND i.severity = 'block')
               FROM orders o WHERE o.status = 'needs_review' ORDER BY o.id""").fetchall()
        st.subheader(f"Needs review ({len(waiting)})")
        if not waiting:
            st.markdown('<div class="cpo-ok">Nothing waiting. Every order is handled.</div>', unsafe_allow_html=True)
        rows = []
        for order_id, who, codes in waiting[:6]:
            pills = "".join(f'<span class="cpo-pill">{ISSUE_TEXT.get(c, c)}</span>'
                            for c in (codes or "").split(",") if c)
            rows.append(f'<div class="cpo-row"><div><span class="id">#{order_id}</span>'
                        f'<span class="who">{who}</span></div><div>{pills}</div></div>')
        if rows:
            st.markdown("".join(rows), unsafe_allow_html=True)
            if len(waiting) > 6:
                st.caption(f"and {len(waiting) - 6} more")
            st.button(f"Open review", type="primary", on_click=go_to, args=("Review",), use_container_width=True)

    st.write("")
    errors = metrics.errors_caught(con, start=start, account=account)
    left, right = st.columns([1.15, 1])
    with left, st.container(border=True):
        total_risk = float(errors["value_usd"].sum()) if not errors.empty else 0.0
        st.subheader(f"Errors caught before entry  ·  {money(total_risk)} at risk")
        if errors.empty:
            st.caption("No problems found in this period.")
        else:
            data = errors.iloc[::-1]
            fig = go.Figure(go.Bar(
                x=data["n"], y=[ISSUE_TEXT.get(c, c) for c in data["code"]], orientation="h",
                marker_color="#F5B547", marker_line_width=0,
                text=[f"{n}  ·  {money(v)}" if v else f"{n}" for n, v in zip(data["n"], data["value_usd"])],
                textposition="outside", cliponaxis=False,
                hovertemplate="%{y}: %{x} times<extra></extra>"))
            dark_layout(fig, 40 + 32 * len(data))
            fig.update_layout(xaxis=dict(visible=False), yaxis=dict(showgrid=False),
                              margin=dict(l=8, r=90, t=4, b=4))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            st.markdown("<p class='cpo-note'>Money at risk = wrong price x quantity, full value of duplicate orders, "
                        "and the extra value of quantity typing mistakes.</p>", unsafe_allow_html=True)
    with right, st.container(border=True):
        st.subheader("Weekly note")
        facts = {
            "period": period, "customer": customer, "orders": k["orders"], "order_lines": k["lines"],
            "automatic_percent": round(k["automatic_pct"], 1), "hours_saved": round(k["hours_saved"], 1),
            "money_saved_usd": round(k["money_saved"], 0), "order_value_usd": round(k["order_value"], 0),
            "orders_waiting_for_review": k["waiting"],
            "problems_caught": {ISSUE_TEXT.get(c, c): int(n) for c, n in zip(errors["code"], errors["n"])}
            if not errors.empty else {},
            "weekly_automatic_percent": [round(x, 1) for x in weekly["automatic_pct"]] if not weekly.empty else [],
        }
        note = st.session_state.get("note") or template_note(facts)
        st.markdown(f'<div class="cpo-noteblock">{note}</div>', unsafe_allow_html=True)
        st.write("")
        if st.button("Rewrite with Claude", help="Claude writes a short note for the owner from the numbers on this page"):
            new_note = None
            try:
                new_note = get_reader().write_summary(facts)
            except Exception as exc:  # show the error, keep the page working
                st.error(f"Could not write the note: {exc}")
            if new_note:
                st.session_state.note = new_note
                st.rerun()
            elif not get_reader().uses_ai:
                st.caption("Set an API key to let Claude write the note.")

    st.write("")
    with st.container(border=True):
        st.subheader("Customers")
        customers_df = metrics.by_customer(con, start=start)
        if customers_df.empty:
            st.caption("No orders yet.")
        else:
            customers_df = customers_df.sort_values("value_usd", ascending=False)
            customers_df["share"] = customers_df["value_usd"] / customers_df["value_usd"].sum() * 100
            customers_df["value_txt"] = customers_df["value_usd"].map(lambda v: f"${v:,.0f}")
            customers_df["problem_rate"] = customers_df["with_problems"] / customers_df["orders"] * 100
            st.dataframe(
                customers_df[["customer", "orders", "value_txt", "share", "problem_rate"]],
                hide_index=True, use_container_width=True, height=min(390, 38 + 35 * len(customers_df)),
                column_config={
                    "customer": st.column_config.TextColumn("Customer"),
                    "orders": st.column_config.NumberColumn("Orders", format="%d"),
                    "value_txt": st.column_config.TextColumn("Order value"),
                    "share": st.column_config.ProgressColumn("Share of value", format="%.1f%%", min_value=0,
                                                             max_value=float(customers_df["share"].max())),
                    "problem_rate": st.column_config.NumberColumn("Orders with problems", format="%.0f%%"),
                })

    st.markdown(
        f"<p class='cpo-note' style='margin-top:12px'>Order value = total of ready, approved and exported orders. "
        f"Time saved = handled orders x "
        f"{k['baseline_minutes']:g} min manual entry, minus real review time. "
        f"Rate ${k['hourly_rate']:.2f}/h (BLS median wage, order clerks). "
        f"AI cost this period: ${k['api_cost']:.2f}."
        + (" Earlier weeks are sample history." if metrics.has_demo_history(con) else "") + "</p>",
        unsafe_allow_html=True)


def template_note(facts: dict) -> str:
    top = max(facts["problems_caught"].items(), key=lambda kv: kv[1], default=None)
    text = (f"{facts['orders']} orders ({facts['order_lines']} lines) went through the order desk. "
            f"{facts['automatic_percent']:.0f}% needed no fix, which saved about {facts['hours_saved']:.1f} hours "
            f"(about ${facts['money_saved_usd']:,.0f}).")
    if top:
        text += f" The most common problem was \"{top[0].lower()}\" ({top[1]} times)."
    if facts["orders_waiting_for_review"]:
        text += f" {facts['orders_waiting_for_review']} order(s) are waiting for review."
    return text


# ---------------------------------------------------------------------------
# Process orders
# ---------------------------------------------------------------------------

def page_process():
    page_header("Process orders", "Drop in today's orders from all customers: PDF, e-mail, Excel, CSV or a photo.")
    files = st.file_uploader("Order files", accept_multiple_files=True,
                             type=["pdf", "eml", "xlsx", "xls", "csv", "txt", "jpg", "jpeg", "png"],
                             label_visibility="collapsed")
    col1, col2 = st.columns([1, 3])
    run_uploaded = col1.button("Process files", type="primary", disabled=not files)
    run_samples = col2.button("Load sample orders")

    paths: list[Path] = []
    if run_uploaded and files:
        upload_dir = DATA / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            target = upload_dir / f.name
            target.write_bytes(f.getbuffer())
            paths.append(target)
    if run_samples:
        paths = sorted((DEMO / "orders").iterdir())

    if paths:
        catalog, reader = get_catalog(), get_reader()
        progress = st.progress(0.0, text="Starting")
        ids = []
        for n, path in enumerate(paths, start=1):
            progress.progress((n - 1) / len(paths), text=f"Reading {path.name}")
            ids.append(process_file(con, path, catalog, reader, INBOX))
        progress.progress(1.0, text="Done")
        st.session_state.last_batch = ids

    ids = st.session_state.get("last_batch")
    if ids:
        frame = con.execute(
            f"""SELECT o.id AS "Order", o.file_name AS "File", coalesce(o.customer_name, o.sender_email) AS "Customer",
                       o.n_lines AS "Lines", o.total AS "Total", o.status AS "Status",
                       (SELECT string_agg(message, ' | ') FROM issues i WHERE i.order_id = o.id) AS "Notes",
                       o.read_by AS "Read by"
                FROM orders o WHERE o.id IN ({','.join('?' * len(ids))}) ORDER BY o.id""", ids).df()
        ready = int((frame["Status"] == "ready").sum())
        review = int((frame["Status"] == "needs_review").sum())
        other = int((frame["Status"] == "not_order").sum())
        a, b, c = st.columns(3)
        a.metric("Ready", ready)
        b.metric("Need a human", review)
        c.metric("Not orders (filtered)", other)
        frame["Notes"] = frame["Notes"].fillna("")
        frame["Status"] = frame["Status"].map({"ready": "✔ Ready", "needs_review": "⚠ Needs a human",
                                               "not_order": "Not an order"}).fillna(frame["Status"])
        st.dataframe(frame, hide_index=True, use_container_width=True,
                     column_config={"Order": st.column_config.NumberColumn(format="%d"),
                                    "Total": st.column_config.NumberColumn(format="$%.2f"),
                                    "Notes": st.column_config.TextColumn(width="large")})
        if (frame["Read by"] == "replay").any():
            st.caption("Read by 'replay' = saved reading of a sample order (no API key set).")


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def show_document(stored_path: str, source_type: str):
    path = Path(stored_path)
    if not path.exists():
        st.caption("Original file not found.")
        return
    if source_type == "pdf":
        import pymupdf
        with pymupdf.open(path) as doc:
            for page_obj in list(doc)[:3]:
                # Crop the empty part of the page so that the text is large enough to read.
                blocks = [pymupdf.Rect(b[:4]) for b in page_obj.get_text("blocks")]
                clip = None
                if blocks:
                    clip = blocks[0]
                    for rect in blocks[1:]:
                        clip |= rect
                    clip = (clip + (-18, -18, 18, 18)) & page_obj.rect
                st.image(page_obj.get_pixmap(dpi=160, clip=clip).tobytes("png"), use_column_width=True)
    elif source_type == "image":
        st.image(str(path), use_column_width=True)
    elif source_type == "email":
        sender, subject, body, attachments = parse_email(path.read_bytes())
        st.markdown(f"**From:** {sender}  \n**Subject:** {subject}")
        st.text(body)
        if attachments:
            st.caption("Attachments: " + ", ".join(n for n, _ in attachments))
    elif source_type in ("excel", "csv"):
        frame = (pd.read_csv(path, header=None, names=range(8)) if source_type == "csv"
                 else pd.read_excel(path, header=None))
        st.dataframe(frame.dropna(how="all", axis=1).fillna(""), hide_index=True, use_container_width=True)
    else:
        st.text(path.read_text(encoding="utf-8", errors="replace")[:4000])


def page_review():
    page_header("Review", "Check the original order, solve each problem, approve.")
    waiting = con.execute(
        """SELECT id, coalesce(customer_name, sender_email, file_name), file_name FROM orders
           WHERE status = 'needs_review' ORDER BY id""").fetchall()
    if not waiting:
        st.success("Nothing to review. Every order is ready or handled.")
        return

    labels = {oid: f"#{oid}  {who}  ({fname})" for oid, who, fname in waiting}
    order_id = st.selectbox(f"{len(waiting)} order(s) need a human", list(labels), format_func=labels.get)
    timer_key = f"started_{order_id}"
    st.session_state.setdefault(timer_key, time.time())

    order = con.execute(
        """SELECT file_name, stored_path, source_type, customer_account, customer_name, sender_email, po_number,
                  order_date, delivery_date, total, notes FROM orders WHERE id = ?""", [order_id]).fetchone()
    (file_name, stored_path, source_type, account, cname, sender, po, odate, ddate, total, notes) = order
    issues = con.execute(
        "SELECT line_no, code, severity, message, value_usd FROM issues WHERE order_id = ? AND NOT resolved "
        "ORDER BY line_no NULLS FIRST", [order_id]).fetchall()
    lines = con.execute(
        """SELECT line_no, raw_text, product_code, product_name, qty, unit, price, amount, match_method, candidates,
                  unit_price_written FROM order_lines WHERE order_id = ? ORDER BY line_no""", [order_id]).fetchall()
    catalog = get_catalog()

    doc_col, work_col = st.columns([1, 1.15])
    with doc_col:
        st.markdown("##### Original order")
        show_document(stored_path, source_type)
    with work_col:
        st.markdown("##### What CleanPO read")
        st.markdown(f"**Customer:** {cname or '—'} {f'({account})' if account else ''}  \n"
                    f"**PO:** {po or '—'} &nbsp;·&nbsp; **Date:** {odate or '—'} &nbsp;·&nbsp; "
                    f"**Delivery:** {ddate or '—'}")
        if lines:
            table = pd.DataFrame(lines, columns=["Line", "As written", "Item", "Product", "Qty", "Unit", "Price",
                                                 "Amount", "Matched by", "_c", "_p"]).drop(columns=["_c", "_p"])
            st.dataframe(table, hide_index=True, use_container_width=True,
                         column_config={"Price": st.column_config.NumberColumn(format="$%.2f"),
                                        "Amount": st.column_config.NumberColumn(format="$%.2f")})
        if notes:
            st.caption(f"Note: {md(notes)}")

        st.markdown(f"##### {len([i for i in issues if i[2] == 'block'])} problem(s) to solve")
        line_changes: dict[int, dict] = {}
        new_account = None
        can_approve = True
        all_products = {code: f"{code}  {p.name}" + ("" if p.active else "  (discontinued)")
                        for code, p in sorted(catalog.products.items())}
        line_map = {l[0]: l for l in lines}

        for line_no, code, severity, message, value in issues:
            if severity != "block":
                st.caption(f"ℹ️ Line {line_no}: {md(message)}" if line_no else f"ℹ️ {md(message)}")
                continue
            title = ISSUE_TEXT.get(code, code) + (f" (line {line_no})" if line_no else "")
            with st.container(border=True):
                st.markdown(f"**⚠ {title}**  \n{md(message)}")
                key = f"{order_id}_{line_no}_{code}"
                if code in ("ITEM_AMBIGUOUS", "ITEM_UNMATCHED", "ITEM_INACTIVE"):
                    cands = [c[0] for c in json.loads(line_map[line_no][9] or "[]")]
                    options = ["(remove this line)"] + [c for c in cands if c in all_products] + \
                              [c for c in all_products if c not in cands]
                    default = 1 if cands and code != "ITEM_INACTIVE" else 0
                    picked = st.selectbox("Product", options, index=default, key=key,
                                          format_func=lambda c: all_products.get(c, c))
                    line_changes.setdefault(line_no, {})
                    if picked == "(remove this line)":
                        line_changes[line_no]["removed"] = True
                    else:
                        line_changes[line_no]["product_code"] = picked
                        if not catalog.products[picked].active:
                            st.error("This product is discontinued. Choose a replacement or remove the line.")
                            can_approve = False
                elif code == "PRICE_MISMATCH":
                    l = line_map[line_no]
                    choice = st.radio("Price", [f"Use price list ${l[6]:,.2f}", f"Keep order price ${l[10]:,.2f}"],
                                      key=key, horizontal=True)
                    if choice.startswith("Keep"):
                        line_changes.setdefault(line_no, {})["price"] = l[10]
                elif code in ("QTY_UNUSUAL", "UNIT_MISMATCH"):
                    l = line_map[line_no]
                    qty = st.number_input(f"Quantity ({l[5]})", min_value=0.0, value=float(l[4]), step=1.0, key=key)
                    line_changes.setdefault(line_no, {})["qty"] = qty
                elif code == "CUSTOMER_UNKNOWN":
                    accounts = {c[0]: f"{c[0]}  {c[1]}" for c in catalog.customers}
                    picked = st.selectbox("Customer account", ["(choose)"] + list(accounts), key=key,
                                          format_func=lambda a: accounts.get(a, a))
                    if picked == "(choose)":
                        can_approve = False
                        st.caption("New customer? Set up the account in your system first, then reload the "
                                   "customer list, or reject this order.")
                    else:
                        new_account = picked
                elif code == "CREDIT_HOLD":
                    if not st.checkbox("A manager approved this order", key=key):
                        can_approve = False
                elif code == "DUPLICATE_ORDER":
                    choice = st.radio("Is this a new order?", ["Duplicate: reject it", "New order: keep it"],
                                      key=key, horizontal=True)
                    if choice.startswith("Duplicate"):
                        can_approve = False
                else:
                    can_approve = False
                    st.caption("This file cannot be approved. Reject it and enter the order by hand.")

        remember = st.checkbox("Remember my product choices for this customer", value=True,
                               help="Adds the choice to the memory list, so the same text is matched next time.")
        b1, b2, _ = st.columns([1, 1, 2])
        seconds = time.time() - st.session_state[timer_key]
        if b1.button("Approve", type="primary", disabled=not can_approve, use_container_width=True):
            approve(con, catalog, order_id, customer_account=new_account, line_changes=line_changes,
                    remember=remember, review_seconds=seconds, note="approved after review")
            st.session_state.pop(timer_key, None)
            st.toast(f"Order #{order_id} approved")
            st.rerun()
        if b2.button("Reject", use_container_width=True):
            reject(con, order_id, review_seconds=seconds)
            st.session_state.pop(timer_key, None)
            st.toast(f"Order #{order_id} rejected")
            st.rerun()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def page_export():
    page_header("Export", "One import file for all approved orders, plus a summary for your records.")
    ids = export.exportable_ids(con)
    frame = export.import_frame(con, ids)
    n_orders = len(ids)
    a, b, c = st.columns(3)
    a.metric("Orders ready to import", n_orders)
    b.metric("Lines", len(frame))
    c.metric("Value", money(float(frame["Amount"].sum())) if len(frame) else "$0")

    if n_orders:
        st.dataframe(frame, hide_index=True, use_container_width=True, height=320,
                     column_config={"UnitPrice": st.column_config.NumberColumn(format="$%.2f"),
                                    "Amount": st.column_config.NumberColumn(format="$%.2f")})
        st.download_button("Download import file (CSV)", export.import_csv(con, ids),
                           file_name=f"cleanpo_import_{export.file_stamp()}.csv", mime="text/csv", type="primary",
                           on_click=export.mark_exported, args=(con, ids))
        st.caption("After the download, these orders are marked as exported, so they are not imported twice. "
                   "The column layout is set to the client's software during setup.")
    else:
        st.info("No orders waiting for export. Process orders, or approve the ones in Review.")

    k = metrics.kpis(con, include_demo=False)
    facts = {"Orders processed": k["orders"], "Order lines": k["lines"], "Automatic (no fix)": k["automatic"],
             "Waiting for review": k["waiting"], "Non-order e-mails filtered": k["not_orders"],
             "Review minutes": round(k["review_minutes"], 1), "Hours saved": round(k["hours_saved"], 2),
             "Money saved USD": round(k["money_saved"], 2), "AI cost USD": round(k["api_cost"], 4),
             "Created": datetime.now().strftime("%Y-%m-%d %H:%M")}
    st.download_button("Download summary (Excel)", export.summary_workbook(con, facts),
                       file_name=f"cleanpo_summary_{export.file_stamp()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def page_settings():
    page_header("Settings", "Lists, time-saved calculation and reading options.")

    st.subheader("Time-saved calculation")
    c1, c2 = st.columns(2)
    baseline = c1.number_input("Manual entry minutes per order", min_value=0.5, step=0.5,
                               value=float(db.get_setting(con, "baseline_minutes_per_order")))
    source = c1.text_input("Where this number comes from", db.get_setting(con, "baseline_source"))
    rate = c2.number_input("Clerk cost per hour (USD)", min_value=1.0, step=0.5,
                           value=float(db.get_setting(con, "hourly_rate_usd")))
    rate_source = c2.text_input("Where this rate comes from", db.get_setting(con, "hourly_rate_source"))

    st.subheader("Reading")
    models = list(PRICES_PER_MTOK)
    current = db.get_setting(con, "model")
    model = st.selectbox("Claude model", models, index=models.index(current) if current in models else 0,
                         format_func=lambda m: f"{m}  (${PRICES_PER_MTOK[m][0]:g} in / ${PRICES_PER_MTOK[m][1]:g} out "
                                               f"per million tokens)")
    if api_key_available():
        st.success("API key found in the ANTHROPIC_API_KEY environment variable.")
    else:
        st.info("No API key found. Set the ANTHROPIC_API_KEY environment variable on this computer and restart "
                "the app. The key is never typed into this page and never saved by CleanPO.")
    company_name = st.text_input("Company name", db.get_setting(con, "company_name"))

    if st.button("Save settings", type="primary"):
        for key, value in [("baseline_minutes_per_order", str(baseline)), ("baseline_source", source),
                           ("hourly_rate_usd", str(rate)), ("hourly_rate_source", rate_source),
                           ("model", model), ("company_name", company_name)]:
            db.set_setting(con, key, value)
        st.toast("Saved")
        st.rerun()

    st.subheader("Master lists")
    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in db.MASTER_FILES if db.has_table(con, t)}
    st.write(", ".join(f"{t.replace('_', ' ')}: **{n}**" for t, n in counts.items()))
    uploads = st.file_uploader("Upload new lists exported from your system (products.csv, customers.csv, "
                               "prices.csv, and optional aliases.csv, customer_item_codes.csv, order_history.csv)",
                               type=["csv"], accept_multiple_files=True)
    if uploads and st.button("Load these lists"):
        folder = DATA / "master"
        folder.mkdir(parents=True, exist_ok=True)
        for f in uploads:
            if f.name in db.MASTER_FILES.values():
                (folder / f.name).write_bytes(f.getbuffer())
        for needed in ("products.csv", "customers.csv", "prices.csv"):
            if not (folder / needed).exists():
                st.error(f"{needed} is required.")
                return
        db.load_master(con, folder)
        st.session_state.pop("catalog", None)
        st.toast("Lists loaded")
        st.rerun()
    learned = con.execute("SELECT count(*) FROM learned_aliases").fetchone()[0]
    st.caption(f"Memory list: {learned} product name(s) learned from reviews.")

    st.subheader("Sample data")
    d1, d2, d3 = st.columns(3)
    if metrics.has_demo_history(con):
        if d1.button("Remove sample history"):
            metrics.clear_demo_history(con)
            st.rerun()
    elif d1.button("Add sample history"):
        metrics.seed_demo_history(con)
        st.rerun()
    if d2.button("Clear processed orders"):
        con.execute("DELETE FROM issues WHERE NOT is_demo")
        con.execute("DELETE FROM order_lines")
        con.execute("DELETE FROM orders WHERE NOT is_demo")
        st.session_state.pop("last_batch", None)
        st.toast("Processed orders cleared")
        st.rerun()
    if d3.button("Reset learned memory"):
        con.execute("DELETE FROM learned_aliases")
        st.session_state.pop("catalog", None)
        st.rerun()


{"Dashboard": page_dashboard, "Process orders": page_process, "Review": page_review,
 "Export": page_export, "Settings": page_settings}[page]()
