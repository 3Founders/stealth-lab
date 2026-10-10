"""Data-reporting tasks: 6 synthetic datasets x 3 reports = 18 tasks, each with a reference report.

Each dataset is generated with a fixed seed (`build()` writes the CSVs next to this file; they are vendored, so the
data never depends on the Python version's random module). Every dataset has two versions:
  <name>.csv         -- the one the agent gets as data.csv in its workspace;
  <name>_hidden.csv  -- the same generator with another seed, used only by the grade (so a report.py that hardcodes
                        the numbers, or only works for the visible rows, fails).

A task is a list of metrics; each metric has a key, the sentence the agent reads, the number of decimals it is
rounded to (None: an exact integer, string, list or mapping without floats) and the reference function. The numbers
in TASK.md are the only definition the agent gets; the reference implements exactly that text.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEDS = {"visible": 1, "hidden": 2}


# ---------------------------------------------------------------- generators

def _dates(rng, start, end):
    days = (end - start).days
    return start + dt.timedelta(days=rng.randrange(days + 1))


def gen_sales(seed):
    rng = random.Random(seed)
    catalog = {"Electronics": [("Headphones", 59.9), ("Charger", 19.5), ("Webcam", 74.0), ("Keyboard", 45.25)],
               "Home": [("Lamp", 32.0), ("Kettle", 27.8), ("Rug", 119.0), ("Mug set", 14.6)],
               "Outdoor": [("Tent", 210.0), ("Lantern", 22.4), ("Backpack", 64.99)],
               "Office": [("Notebook", 3.2), ("Desk chair", 159.0), ("Stapler", 8.75)]}
    regions = ["North", "South", "East", "West"]
    channels = ["web", "store", "phone"]
    rows = []
    for i in range(640):
        cat = rng.choices(list(catalog), weights=[4, 3, 2, 2])[0]
        prod, base = rng.choice(catalog[cat])
        price = round(base * rng.choice([1, 1, 1, 0.95, 1.05]), 2)
        d = _dates(rng, dt.date(2023, 1, 1), dt.date(2024, 12, 31))
        rows.append({"order_id": 10000 + i, "order_date": d.isoformat(),
                     "region": rng.choices(regions, weights=[3, 2, 2, 3])[0],
                     "channel": rng.choices(channels, weights=[5, 3, 1])[0], "category": cat, "product": prod,
                     "units": rng.choices([1, 2, 3, 4, 5, 8, 10], weights=[30, 20, 12, 8, 5, 2, 1])[0],
                     "unit_price": f"{price:.2f}", "discount": rng.choice(["0", "0", "0", "0.05", "0.1", "0.15", "0.2"]),
                     "returned": 1 if rng.random() < 0.07 else 0})
    rows.sort(key=lambda r: (r["order_date"], r["order_id"]))
    return rows


def gen_web(seed):
    rng = random.Random(seed)
    countries = ["US", "DE", "IN", "BR", "GB", "FR", "JP"]
    users = [f"u{n:04d}" for n in range(1, 241)]
    rows = []
    sid = 500000
    day = dt.date(2024, 3, 4)
    for d in range(42):
        date = day + dt.timedelta(days=d)
        for _ in range(rng.randint(8, 30) if date.weekday() < 5 else rng.randint(4, 14)):
            sid += rng.randint(1, 3)
            pages = rng.choices([1, 2, 3, 4, 6, 9, 15], weights=[25, 18, 15, 12, 10, 6, 2])[0]
            conv = 1 if pages >= 3 and rng.random() < 0.18 else 0
            rows.append({"session_id": sid, "date": date.isoformat(), "user_id": rng.choice(users),
                         "device": rng.choices(["desktop", "mobile", "tablet"], weights=[5, 6, 1])[0],
                         "country": rng.choices(countries, weights=[6, 3, 4, 2, 3, 2, 1])[0], "pages": pages,
                         "duration_s": pages * rng.randint(8, 70) + rng.randint(0, 30), "converted": conv,
                         "revenue": f"{round(rng.uniform(9, 240), 2):.2f}" if conv else "0.00"})
    return rows


SLA = {"urgent": 4, "high": 24, "medium": 72, "low": 168}


def gen_tickets(seed):
    rng = random.Random(seed)
    rows = []
    start = dt.datetime(2024, 1, 1, 8, 0)
    for i in range(520):
        opened = start + dt.timedelta(minutes=rng.randrange(0, 181 * 24 * 60))
        prio = rng.choices(list(SLA), weights=[1, 3, 6, 5])[0]
        team = rng.choice(["billing", "platform", "accounts", "mobile"])
        hours = rng.expovariate(1 / (SLA[prio] * rng.choice([0.4, 0.7, 1.0, 1.6])))
        closed = opened + dt.timedelta(minutes=max(5, int(hours * 60)))
        is_open = closed > dt.datetime(2024, 7, 1) or rng.random() < 0.04
        csat = "" if is_open or rng.random() < 0.45 else str(rng.choices([1, 2, 3, 4, 5], weights=[1, 1, 2, 4, 5])[0])
        rows.append({"ticket_id": f"T{i + 1:05d}", "opened": opened.strftime("%Y-%m-%d %H:%M"),
                     "closed": "" if is_open else closed.strftime("%Y-%m-%d %H:%M"), "priority": prio, "team": team,
                     "channel": rng.choices(["email", "chat", "phone"], weights=[5, 4, 2])[0], "csat": csat,
                     "reopened": 0 if is_open else (1 if rng.random() < 0.08 else 0)})
    rows.sort(key=lambda r: (r["opened"], r["ticket_id"]))
    return rows


def gen_inventory(seed):
    rng = random.Random(seed)
    skus = ["SKU-A1", "SKU-A2", "SKU-B1", "SKU-B2", "SKU-C1", "SKU-C2", "SKU-D1", "SKU-E9"]
    cost = {s: rng.uniform(2, 60) for s in skus}
    rows = []
    for i in range(700):
        d = _dates(rng, dt.date(2024, 1, 1), dt.date(2024, 6, 30))
        sku = rng.choice(skus[:-1]) if rng.random() < 0.97 else "SKU-E9"
        kind = rng.choices(["receipt", "shipment", "adjustment"], weights=[3, 6, 1])[0]
        if kind == "receipt":
            qty, uc = rng.choice([20, 40, 50, 100, 120]), f"{cost[sku] * rng.uniform(0.9, 1.1):.2f}"
        elif kind == "shipment":
            qty, uc = rng.randint(1, 30), ""
        else:
            qty, uc = rng.choice([-5, -3, -2, -1, 1, 2, 4]), ""
        rows.append({"date": d.isoformat(), "sku": sku, "warehouse": rng.choices(["AMS", "BER", "MAD"],
                                                                                 weights=[5, 3, 2])[0],
                     "type": kind, "qty": qty, "unit_cost": uc})
    rows.sort(key=lambda r: (r["date"], r["sku"], r["warehouse"]))
    return rows


def gen_weather(seed):
    rng = random.Random(seed)
    stations = {"OSLO": -3.0, "ROMA": 9.0, "WIEN": 2.0}
    rows = []
    for st, base in stations.items():
        t = base
        skip = {rng.randrange(14 * 24) for _ in range(6)}
        for h in range(14 * 24):
            if h in skip:
                continue
            ts = dt.datetime(2024, 1, 10) + dt.timedelta(hours=h)
            t += rng.gauss(0, 0.8) + 0.25 * (base + 4 * math.sin((ts.hour - 9) / 24 * 2 * math.pi) - t)
            rain = rng.random() < 0.12
            rows.append({"station": st, "timestamp": ts.strftime("%Y-%m-%d %H:00"),
                         "temp_c": "" if rng.random() < 0.04 else f"{t:.1f}",
                         "humidity": "" if rng.random() < 0.06 else str(rng.randint(45, 99)),
                         "precip_mm": f"{rng.choice([0.2, 0.5, 1.1, 2.4, 4.0]) if rain else 0:.1f}",
                         "wind_kmh": str(rng.randint(0, 45 if st == "WIEN" else 30))})
            if rng.random() < 0.01:
                rows.append(dict(rows[-1]))
    return rows


def gen_staff(seed):
    rng = random.Random(seed)
    depts = {"Engineering": 95000, "Sales": 70000, "Support": 52000, "Finance": 80000, "People": 60000}
    rows = []
    for i in range(260):
        dept = rng.choices(list(depts), weights=[5, 3, 3, 1, 1])[0]
        level = rng.choices(["L1", "L2", "L3", "L4", "L5"], weights=[4, 5, 4, 2, 1])[0]
        sal = int(round(depts[dept] * (0.7 + 0.22 * int(level[1])) * rng.uniform(0.85, 1.15), -2))
        hired = _dates(rng, dt.date(2014, 1, 1), dt.date(2024, 12, 15))
        rows.append({"employee_id": 1001 + i, "dept": dept, "level": level, "location": rng.choices(
            ["Berlin", "Lisbon", "Remote", "Toronto"], weights=[4, 2, 3, 2])[0], "hire_date": hired.isoformat(),
            "salary": sal, "bonus_pct": rng.choice([0, 5, 5, 8, 10, 12.5, 15]),
            "status": "left" if rng.random() < 0.14 else "active"})
    return rows


GENERATORS = {"sales": gen_sales, "web": gen_web, "tickets": gen_tickets, "inventory": gen_inventory,
              "weather": gen_weather, "staff": gen_staff}


def build():
    for name, gen in GENERATORS.items():
        for which, seed in SEEDS.items():
            rows = gen(seed)
            path = HERE / (f"{name}.csv" if which == "visible" else f"{name}_hidden.csv")
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
                w.writeheader()
                w.writerows(rows)


def csv_path(dataset, hidden=False):
    return HERE / (f"{dataset}_hidden.csv" if hidden else f"{dataset}.csv")


def load(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- helpers for the references

def r2(x, d=2):
    return round(x, d)


def by(rows, key, val, agg=sum):
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(val(r))
    return {k: agg(v) for k, v in sorted(g.items())}


def argmax(d, prefer_low=False):
    """Key with the highest value; ties -> the smallest key."""
    best = max(d.values()) if not prefer_low else min(d.values())
    return sorted(k for k, v in d.items() if v == best)[0]


def top(d, n):
    return [k for k, _ in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def pct(a, b, d=1):
    return round(100 * a / b, d) if b else None


def nearest_rank(vals, p):
    s = sorted(vals)
    return s[max(1, math.ceil(p / 100 * len(s))) - 1]


def M(key, text, dp, fn):
    return {"key": key, "text": text, "dp": dp, "fn": fn}


# ---------------------------------------------------------------- sales

def rev(r):
    return int(r["units"]) * float(r["unit_price"]) * (1 - float(r["discount"]))


SALES_INTRO = ("`data.csv` holds one row per web-shop order line: `order_id`, `order_date` (YYYY-MM-DD), `region`, "
               "`channel`, `category`, `product`, `units` (integer), `unit_price`, `discount` (a fraction, e.g. 0.15 = "
               "15% off) and `returned` (1 if the order was returned, else 0). An order's **revenue** is "
               "`units * unit_price * (1 - discount)`; do not round individual rows.")


def _month_rev(rows):
    return by([{**r, "month": r["order_date"][:7]} for r in rows], "month", rev)


TASKS = {}

TASKS["sales_summary"] = ("sales", SALES_INTRO, [
    M("total_orders", "number of rows", None, lambda rs: len(rs)),
    M("total_units", "sum of `units`", None, lambda rs: sum(int(r["units"]) for r in rs)),
    M("gross_revenue", "sum of revenue over all rows (returned ones included)", 2,
      lambda rs: r2(sum(rev(r) for r in rs))),
    M("revenue_by_region", "object: region -> sum of revenue", 2,
      lambda rs: {k: r2(v) for k, v in by(rs, "region", rev).items()}),
    M("top_region", "the region with the highest revenue (ties: alphabetically first)", None,
      lambda rs: argmax(by(rs, "region", rev))),
    M("avg_order_value", "gross revenue divided by the number of rows", 2,
      lambda rs: r2(sum(rev(r) for r in rs) / len(rs))),
    M("return_rate_pct", "percentage of rows with `returned` = 1", 1,
      lambda rs: pct(sum(int(r["returned"]) for r in rs), len(rs))),
    M("orders_by_channel", "object: channel -> number of rows", None, lambda rs: by(rs, "channel", lambda r: 1)),
])

TASKS["sales_trend"] = ("sales", SALES_INTRO, [
    M("revenue_by_month", "object: month `YYYY-MM` (of `order_date`) -> sum of revenue, for every month that has "
      "rows", 2, lambda rs: {k: r2(v) for k, v in _month_rev(rs).items()}),
    M("best_month", "the month `YYYY-MM` with the highest revenue (ties: the earliest)", None,
      lambda rs: argmax(_month_rev(rs))),
    M("mom_growth_pct_last", "percentage change of the revenue of the LAST month that has rows versus the month "
      "before it in the data: 100 * (last - previous) / previous", 1,
      lambda rs: (lambda m: r2(100 * (m[-1] - m[-2]) / m[-2], 1))(list(_month_rev(rs).values()))),
    M("yoy_growth_pct", "percentage change of 2024 revenue versus 2023 revenue", 1,
      lambda rs: (lambda a, b: r2(100 * (b - a) / a, 1))(
          sum(rev(r) for r in rs if r["order_date"][:4] == "2023"),
          sum(rev(r) for r in rs if r["order_date"][:4] == "2024"))),
    M("units_2024_q4", "sum of `units` for orders dated 2024-10-01 to 2024-12-31", None,
      lambda rs: sum(int(r["units"]) for r in rs if "2024-10-01" <= r["order_date"] <= "2024-12-31")),
    M("months_above_avg", "number of months whose revenue is strictly above the mean monthly revenue (mean over the "
      "months that have rows)", None,
      lambda rs: (lambda m: sum(1 for v in m if v > sum(m) / len(m)))(list(_month_rev(rs).values()))),
    M("net_revenue", "sum of revenue over rows that were NOT returned", 2,
      lambda rs: r2(sum(rev(r) for r in rs if r["returned"] == "0"))),
    M("weekend_revenue_share_pct", "percentage of total revenue from orders dated on a Saturday or Sunday", 1,
      lambda rs: pct(sum(rev(r) for r in rs if dt.date.fromisoformat(r["order_date"]).weekday() >= 5),
                     sum(rev(r) for r in rs))),
])

TASKS["sales_products"] = ("sales", SALES_INTRO, [
    M("top_products", "list of the 5 product names with the highest revenue, highest first (ties: alphabetical)",
      None, lambda rs: top(by(rs, "product", rev), 5)),
    M("revenue_share_by_category_pct", "object: category -> percentage of total revenue", 1,
      lambda rs: {k: pct(v, sum(rev(r) for r in rs)) for k, v in by(rs, "category", rev).items()}),
    M("avg_discount_by_category", "object: category -> mean of `discount` over its rows", 3,
      lambda rs: {k: r2(v, 3) for k, v in by(rs, "category", lambda r: float(r["discount"]), statistics.mean).items()}),
    M("units_per_order_by_channel", "object: channel -> mean `units` per row", 2,
      lambda rs: {k: r2(v) for k, v in by(rs, "channel", lambda r: int(r["units"]), statistics.mean).items()}),
    M("returned_revenue", "sum of revenue over returned rows", 2,
      lambda rs: r2(sum(rev(r) for r in rs if r["returned"] == "1"))),
    M("products_never_returned", "alphabetically sorted list of products that have no returned row", None,
      lambda rs: sorted({r["product"] for r in rs} - {r["product"] for r in rs if r["returned"] == "1"})),
    M("median_unit_price", "median of `unit_price` over all rows (the mean of the two middle values when the count "
      "is even)", 2, lambda rs: r2(statistics.median(float(r["unit_price"]) for r in rs))),
    M("big_orders", "number of rows whose revenue is at least 250", None,
      lambda rs: sum(1 for r in rs if rev(r) >= 250)),
])

# ---------------------------------------------------------------- web sessions

WEB_INTRO = ("`data.csv` holds one row per website session: `session_id` (integer; larger ids are later sessions), "
             "`date` (YYYY-MM-DD), `user_id`, `device`, `country`, `pages` (pages viewed), `duration_s` (seconds), "
             "`converted` (1 if the session made a purchase) and `revenue` (0.00 when not converted).")


def _f(col):
    return lambda r: float(r[col])


def _i(col):
    return lambda r: int(r[col])


TASKS["web_overview"] = ("web", WEB_INTRO, [
    M("sessions", "number of sessions", None, lambda rs: len(rs)),
    M("unique_users", "number of distinct `user_id`s", None, lambda rs: len({r["user_id"] for r in rs})),
    M("conversion_rate_pct", "percentage of sessions with `converted` = 1", 2,
      lambda rs: pct(sum(int(r["converted"]) for r in rs), len(rs), 2)),
    M("revenue_total", "sum of `revenue`", 2, lambda rs: r2(sum(float(r["revenue"]) for r in rs))),
    M("revenue_per_session", "total revenue divided by the number of sessions", 4,
      lambda rs: r2(sum(float(r["revenue"]) for r in rs) / len(rs), 4)),
    M("sessions_by_device", "object: device -> number of sessions", None, lambda rs: by(rs, "device", lambda r: 1)),
    M("avg_pages", "mean of `pages`", 2, lambda rs: r2(statistics.mean(int(r["pages"]) for r in rs))),
    M("bounce_rate_pct", "percentage of sessions with exactly 1 page", 1,
      lambda rs: pct(sum(1 for r in rs if r["pages"] == "1"), len(rs))),
])


def _conv_users(rs):
    return {r["user_id"] for r in rs if r["converted"] == "1"}


TASKS["web_countries"] = ("web", WEB_INTRO, [
    M("top_countries_by_revenue", "list of the 3 countries with the highest revenue, highest first (ties: "
      "alphabetical)", None, lambda rs: top(by(rs, "country", _f("revenue")), 3)),
    M("conversion_rate_by_country_pct", "object: country -> percentage of its sessions that converted", 1,
      lambda rs: {k: pct(v, len([r for r in rs if r["country"] == k]))
                  for k, v in by(rs, "country", _i("converted")).items()}),
    M("median_duration_by_device", "object: device -> median `duration_s` (mean of the two middle values when the "
      "count is even)", 1, lambda rs: {k: r2(v, 1) for k, v in by(rs, "device", _i("duration_s"),
                                                                     statistics.median).items()}),
    M("users_with_multiple_sessions", "number of users with more than one session", None,
      lambda rs: sum(1 for v in Counter(r["user_id"] for r in rs).values() if v > 1)),
    M("avg_revenue_per_converting_user", "total revenue divided by the number of distinct users with at least one "
      "converted session", 2, lambda rs: r2(sum(float(r["revenue"]) for r in rs) / len(_conv_users(rs)))),
    M("longest_session_id", "`session_id` (integer) of the session with the largest `duration_s` (ties: the smallest "
      "id)", None, lambda rs: min(int(r["session_id"]) for r in rs
                                  if int(r["duration_s"]) == max(int(x["duration_s"]) for x in rs))),
    M("revenue_by_country", "object: country -> sum of revenue", 2,
      lambda rs: {k: r2(v) for k, v in by(rs, "country", _f("revenue")).items()}),
])

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _iso_week(s):
    y, w, _ = dt.date.fromisoformat(s).isocalendar()
    return f"{y:04d}-W{w:02d}"


def _returning(rs):
    seen, n = set(), 0
    for r in sorted(rs, key=lambda r: int(r["session_id"])):
        n += r["user_id"] in seen
        seen.add(r["user_id"])
    return n


TASKS["web_calendar"] = ("web", WEB_INTRO, [
    M("sessions_by_weekday", "object with all 7 keys `Mon` .. `Sun` -> number of sessions on that weekday (0 when "
      "none)", None, lambda rs: {d: sum(1 for r in rs if WEEKDAYS[dt.date.fromisoformat(r["date"]).weekday()] == d)
                                 for d in WEEKDAYS}),
    M("busiest_day", "the `date` with the most sessions (ties: the earliest)", None,
      lambda rs: argmax(Counter(r["date"] for r in rs))),
    M("revenue_by_week", "object: ISO week of `date`, formatted `YYYY-Www` (ISO year, `W`, 2-digit ISO week, e.g. "
      "`2024-W09`) -> sum of revenue", 2,
      lambda rs: {k: r2(v) for k, v in by([{**r, "wk": _iso_week(r["date"])} for r in rs], "wk", _f("revenue")).items()}),
    M("conversion_rate_by_device_pct", "object: device -> percentage of its sessions that converted", 2,
      lambda rs: {k: pct(v, len([r for r in rs if r["device"] == k]), 2)
                  for k, v in by(rs, "device", _i("converted")).items()}),
    M("returning_session_pct", "percentage of sessions whose user has another session with a smaller `session_id`",
      1, lambda rs: pct(_returning(rs), len(rs))),
    M("days_without_revenue", "number of distinct dates in the data whose sessions have zero total revenue", None,
      lambda rs: sum(1 for v in by(rs, "date", _f("revenue")).values() if v == 0)),
    M("weekend_sessions_pct", "percentage of sessions on a Saturday or Sunday", 1,
      lambda rs: pct(sum(1 for r in rs if dt.date.fromisoformat(r["date"]).weekday() >= 5), len(rs))),
])

# ---------------------------------------------------------------- support tickets

TICKETS_INTRO = ("`data.csv` holds one row per support ticket: `ticket_id`, `opened` and `closed` (`YYYY-MM-DD HH:MM`; "
                 "`closed` is empty while the ticket is open), `priority` (low, medium, high, urgent), `team`, "
                 "`channel`, `csat` (customer rating 1-5, empty when not rated) and `reopened` (1/0). A closed "
                 "ticket's **resolution time** is `closed - opened` in hours (minutes count: 90 minutes = 1.5 h). "
                 "SLA targets in hours: urgent 4, high 24, medium 72, low 168.")


def _ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")


def _res_h(r):
    return (_ts(r["closed"]) - _ts(r["opened"])).total_seconds() / 3600


def _closed(rs):
    return [r for r in rs if r["closed"]]


def _breach(r):
    return _res_h(r) > SLA[r["priority"]]


TASKS["tickets_overview"] = ("tickets", TICKETS_INTRO, [
    M("total_tickets", "number of tickets", None, lambda rs: len(rs)),
    M("open_tickets", "number of tickets with an empty `closed`", None, lambda rs: sum(1 for r in rs if not r["closed"])),
    M("tickets_by_priority", "object: priority -> number of tickets", None, lambda rs: by(rs, "priority", lambda r: 1)),
    M("avg_resolution_hours", "mean resolution time of closed tickets", 2,
      lambda rs: r2(statistics.mean(_res_h(r) for r in _closed(rs)))),
    M("median_resolution_hours_by_priority", "object: priority -> median resolution time of its closed tickets "
      "(mean of the two middle values when the count is even)", 2,
      lambda rs: {k: r2(v) for k, v in by(_closed(rs), "priority", _res_h, statistics.median).items()}),
    M("csat_avg", "mean of the non-empty `csat` values", 2,
      lambda rs: r2(statistics.mean(int(r["csat"]) for r in rs if r["csat"]))),
    M("csat_response_rate_pct", "percentage of CLOSED tickets that have a `csat`", 1,
      lambda rs: pct(sum(1 for r in _closed(rs) if r["csat"]), len(_closed(rs)))),
    M("reopen_rate_pct", "percentage of CLOSED tickets with `reopened` = 1", 1,
      lambda rs: pct(sum(1 for r in _closed(rs) if r["reopened"] == "1"), len(_closed(rs)))),
])

SNAPSHOT = dt.datetime(2024, 7, 1, 0, 0)


def _compliance_by_team(rs):
    return {k: pct(sum(1 for r in v if not _breach(r)), len(v))
            for k, v in by(_closed(rs), "team", lambda r: r, list).items()}


TASKS["tickets_sla"] = ("tickets", TICKETS_INTRO, [
    M("sla_breaches", "number of closed tickets whose resolution time is greater than their priority's SLA target",
      None, lambda rs: sum(1 for r in _closed(rs) if _breach(r))),
    M("sla_compliance_pct_by_team", "object: team -> percentage of its closed tickets resolved within the SLA "
      "target (resolution time <= target)", 1, _compliance_by_team),
    M("worst_team", "the team with the lowest `sla_compliance_pct_by_team` value as rounded (ties: alphabetically "
      "first)", None, lambda rs: argmax(_compliance_by_team(rs), prefer_low=True)),
    M("open_overdue", "number of OPEN tickets for which more hours than the SLA target have passed between `opened` "
      "and the snapshot time 2024-07-01 00:00", None,
      lambda rs: sum(1 for r in rs if not r["closed"] and (SNAPSHOT - _ts(r["opened"])).total_seconds() / 3600
                     > SLA[r["priority"]])),
    M("tickets_by_channel", "object: channel -> number of tickets", None, lambda rs: by(rs, "channel", lambda r: 1)),
    M("p90_resolution_hours", "90th percentile of the closed tickets' resolution time, nearest-rank method: sort "
      "ascending and take the value at 1-based position ceil(0.9 * n)", 2,
      lambda rs: r2(nearest_rank([_res_h(r) for r in _closed(rs)], 90))),
    M("breach_rate_pct_by_priority", "object: priority -> percentage of its closed tickets that breached the SLA", 1,
      lambda rs: {k: pct(sum(1 for r in v if _breach(r)), len(v))
                  for k, v in by(_closed(rs), "priority", lambda r: r, list).items()}),
])


def _months(rs):
    return sorted({r["opened"][:7] for r in rs})


def _next_month(m):
    y, mo = int(m[:4]), int(m[5:])
    return f"{y + (mo == 12):04d}-{mo % 12 + 1:02d}"


def _backlog(rs):
    out = {}
    for m in _months(rs):
        cut = _next_month(m) + "-01 00:00"
        out[m] = sum(1 for r in rs if r["opened"] < cut and (not r["closed"] or r["closed"] >= cut))
    return out


TASKS["tickets_monthly"] = ("tickets", TICKETS_INTRO, [
    M("opened_by_month", "object: month `YYYY-MM` of `opened` -> number of tickets", None,
      lambda rs: dict(sorted(Counter(r["opened"][:7] for r in rs).items()))),
    M("closed_by_month", "object: month `YYYY-MM` of `closed` -> number of closed tickets", None,
      lambda rs: dict(sorted(Counter(r["closed"][:7] for r in _closed(rs)).items()))),
    M("backlog_end_of_month", "object: for each month that appears in `opened`, the number of tickets opened before "
      "the first moment of the next month and still open at that moment (`closed` empty, or `closed` at or after "
      "it)", None, _backlog),
    M("team_with_most_tickets", "the team with the most tickets (ties: alphabetically first)", None,
      lambda rs: argmax(Counter(r["team"] for r in rs))),
    M("avg_csat_by_team", "object: team -> mean of its non-empty `csat` values", 2,
      lambda rs: {k: r2(statistics.mean(v)) for k, v in by([r for r in rs if r["csat"]], "team",
                                                            lambda r: int(r["csat"]), list).items()}),
    M("urgent_share_pct", "percentage of tickets with priority urgent", 1,
      lambda rs: pct(sum(1 for r in rs if r["priority"] == "urgent"), len(rs))),
    M("busiest_hour", "hour of day (integer 0-23) of `opened` with the most tickets (ties: the smallest hour)", None,
      lambda rs: argmax(Counter(int(r["opened"][11:13]) for r in rs))),
])

# ---------------------------------------------------------------- inventory

INV_INTRO = ("`data.csv` holds one row per stock movement: `date`, `sku`, `warehouse`, `type` and `qty`. For "
             "`type` = receipt or shipment, `qty` is a positive number of units received or shipped; for `type` = "
             "adjustment, `qty` is signed (+ found, - lost). `unit_cost` is filled only on receipts. **Stock** of a "
             "SKU = receipts - shipments + adjustments.")


def _signed(r):
    q = int(r["qty"])
    return -q if r["type"] == "shipment" else q


def _stock(rs):
    return by(rs, "sku", _signed)


def _avg_cost(rs):
    rec = [r for r in rs if r["type"] == "receipt"]
    q = by(rec, "sku", _i("qty"))
    c = by(rec, "sku", lambda r: int(r["qty"]) * float(r["unit_cost"]))
    return {k: c[k] / q[k] for k in q}


TASKS["inventory_stock"] = ("inventory", INV_INTRO, [
    M("stock_on_hand", "object: sku -> stock over all warehouses", None, _stock),
    M("stock_by_warehouse", "object: warehouse -> object: sku -> stock in that warehouse (only SKUs with at least "
      "one row in that warehouse)", None,
      lambda rs: {w: _stock([r for r in rs if r["warehouse"] == w]) for w in sorted({r["warehouse"] for r in rs})}),
    M("negative_stock_skus", "alphabetically sorted list of SKUs whose total stock is below 0", None,
      lambda rs: sorted(k for k, v in _stock(rs).items() if v < 0)),
    M("total_received_units", "sum of `qty` over receipts", None,
      lambda rs: sum(int(r["qty"]) for r in rs if r["type"] == "receipt")),
    M("total_shipped_units", "sum of `qty` over shipments", None,
      lambda rs: sum(int(r["qty"]) for r in rs if r["type"] == "shipment")),
    M("avg_receipt_cost_by_sku", "object: sku -> quantity-weighted mean `unit_cost` of its receipts "
      "(sum of qty*unit_cost / sum of qty); SKUs without receipts are left out", 4,
      lambda rs: {k: r2(v, 4) for k, v in _avg_cost(rs).items()}),
    M("inventory_value", "sum over SKUs that have receipts of max(stock, 0) * that SKU's weighted mean receipt cost "
      "(unrounded)", 2,
      lambda rs: r2(sum(max(_stock(rs)[k], 0) * c for k, c in _avg_cost(rs).items()))),
    M("busiest_warehouse", "the warehouse with the most rows (ties: alphabetically first)", None,
      lambda rs: argmax(Counter(r["warehouse"] for r in rs))),
])


def _largest_receipt(rs):
    rec = [r for r in rs if r["type"] == "receipt"]
    mx = max(int(r["qty"]) for r in rec)
    r = sorted((r for r in rec if int(r["qty"]) == mx), key=lambda r: (r["date"], r["sku"], r["warehouse"]))[0]
    return {"date": r["date"], "sku": r["sku"], "warehouse": r["warehouse"], "qty": mx}


TASKS["inventory_flow"] = ("inventory", INV_INTRO, [
    M("shipped_units_by_month", "object: month `YYYY-MM` -> shipped units (only months that have shipments)", None,
      lambda rs: by([{**r, "m": r["date"][:7]} for r in rs if r["type"] == "shipment"], "m", _i("qty"))),
    M("top_shipped_skus", "list of the 3 SKUs with the most shipped units, most first (ties: alphabetical)", None,
      lambda rs: top(by([r for r in rs if r["type"] == "shipment"], "sku", _i("qty")), 3)),
    M("adjustment_net_by_warehouse", "object: warehouse -> sum of adjustment `qty` (only warehouses with "
      "adjustments)", None, lambda rs: by([r for r in rs if r["type"] == "adjustment"], "warehouse", _i("qty"))),
    M("days_with_shipments", "number of distinct dates with at least one shipment", None,
      lambda rs: len({r["date"] for r in rs if r["type"] == "shipment"})),
    M("avg_shipment_size", "mean `qty` over shipment rows", 2,
      lambda rs: r2(statistics.mean(int(r["qty"]) for r in rs if r["type"] == "shipment"))),
    M("largest_receipt", "object with keys `date`, `sku`, `warehouse`, `qty` (integer) for the receipt with the "
      "largest `qty` (ties: earliest date, then smallest sku, then smallest warehouse)", None, _largest_receipt),
    M("movements_by_type", "object: type -> number of rows", None, lambda rs: by(rs, "type", lambda r: 1)),
])


def _ratio(rs):
    sh = by([r for r in rs if r["type"] == "shipment"], "sku", _i("qty"))
    rc = by([r for r in rs if r["type"] == "receipt"], "sku", _i("qty"))
    return {k: (r2(sh.get(k, 0) / rc[k], 3) if rc.get(k) else None) for k in sorted({r["sku"] for r in rs})}


TASKS["inventory_skus"] = ("inventory", INV_INTRO, [
    M("ship_to_receipt_ratio", "object: sku (every SKU in the data) -> shipped units / received units, or null "
      "when it has no receipts", 3, _ratio),
    M("first_receipt_date", "object: sku -> the earliest `date` of its receipts (SKUs with receipts only)", None,
      lambda rs: by([r for r in rs if r["type"] == "receipt"], "sku", lambda r: r["date"], min)),
    M("last_shipment_date", "object: sku -> the latest `date` of its shipments (SKUs with shipments only)", None,
      lambda rs: by([r for r in rs if r["type"] == "shipment"], "sku", lambda r: r["date"], max)),
    M("receipt_cost_by_warehouse", "object: warehouse -> sum of qty*unit_cost over its receipts", 2,
      lambda rs: {k: r2(v) for k, v in by([r for r in rs if r["type"] == "receipt"], "warehouse",
                                          lambda r: int(r["qty"]) * float(r["unit_cost"])).items()}),
    M("skus_without_shipments", "alphabetically sorted list of SKUs that never had a shipment", None,
      lambda rs: sorted({r["sku"] for r in rs} - {r["sku"] for r in rs if r["type"] == "shipment"})),
    M("receipts_by_warehouse", "object: warehouse -> number of receipt rows", None,
      lambda rs: by([r for r in rs if r["type"] == "receipt"], "warehouse", lambda r: 1)),
    M("rows_per_sku", "object: sku -> number of rows", None, lambda rs: by(rs, "sku", lambda r: 1)),
])

# ---------------------------------------------------------------- weather

WX_INTRO = ("`data.csv` holds hourly readings from weather stations: `station`, `timestamp` (`YYYY-MM-DD HH:00`), "
            "`temp_c`, `humidity` (%), `precip_mm` and `wind_kmh`. `temp_c` and `humidity` may be EMPTY (a missing "
            "reading); unless a metric says otherwise, ignore missing values. Use every row as given (do not "
            "de-duplicate) unless a metric says otherwise.")


def _num(col):
    return lambda r: float(r[col])


def _has(col):
    return lambda r: r[col] != ""


def _max_temp(rs):
    v = [r for r in rs if r["temp_c"]]
    mx = max(float(r["temp_c"]) for r in v)
    r = sorted((r for r in v if float(r["temp_c"]) == mx), key=lambda r: (r["timestamp"], r["station"]))[0]
    return {"station": r["station"], "timestamp": r["timestamp"], "temp_c": mx}


TASKS["weather_summary"] = ("weather", WX_INTRO, [
    M("readings", "number of rows", None, lambda rs: len(rs)),
    M("missing_temp", "number of rows with an empty `temp_c`", None, lambda rs: sum(1 for r in rs if not r["temp_c"])),
    M("mean_temp_by_station", "object: station -> mean of its non-missing `temp_c`", 2,
      lambda rs: {k: r2(v) for k, v in by([r for r in rs if r["temp_c"]], "station", _num("temp_c"),
                                          statistics.mean).items()}),
    M("max_temp", "object with keys `station`, `timestamp`, `temp_c` (number) for the highest `temp_c` (ties: "
      "earliest timestamp, then smallest station)", None, _max_temp),
    M("total_precip_by_station", "object: station -> sum of `precip_mm`", 1,
      lambda rs: {k: r2(v, 1) for k, v in by(rs, "station", _num("precip_mm")).items()}),
    M("warmest_day", "the date `YYYY-MM-DD` whose non-missing `temp_c` values (all stations together) have the "
      "highest mean (ties: earliest)", None,
      lambda rs: argmax(by([{**r, "d": r["timestamp"][:10]} for r in rs if r["temp_c"]], "d", _num("temp_c"),
                           statistics.mean))),
    M("wind_max_by_station", "object: station -> the largest `wind_kmh` (integer)", None,
      lambda rs: by(rs, "station", _i("wind_kmh"), max)),
])


def _dry_streak(rows):
    best = cur = 0
    for r in sorted(rows, key=lambda r: r["timestamp"]):
        cur = cur + 1 if float(r["precip_mm"]) == 0 else 0
        best = max(best, cur)
    return best


TASKS["weather_stations"] = ("weather", WX_INTRO, [
    M("rainy_days_by_station", "object: station -> number of dates on which its `precip_mm` sums to at least 1.0",
      None, lambda rs: {s: sum(1 for v in by([{**r, "d": r["timestamp"][:10]} for r in rs if r["station"] == s], "d",
                                             _num("precip_mm")).values() if v >= 1.0 - 1e-9)
                        for s in sorted({r["station"] for r in rs})}),
    M("longest_dry_streak_by_station", "object: station -> the largest number of consecutive rows (the station's "
      "rows in timestamp order, a stable sort keeping file order for equal timestamps) with `precip_mm` = 0", None,
      lambda rs: {s: _dry_streak([r for r in rs if r["station"] == s]) for s in sorted({r["station"] for r in rs})}),
    M("freezing_hours_by_station", "object: station -> number of rows with a non-missing `temp_c` <= 0", None,
      lambda rs: {s: sum(1 for r in rs if r["station"] == s and r["temp_c"] and float(r["temp_c"]) <= 0)
                  for s in sorted({r["station"] for r in rs})}),
    M("avg_humidity_by_station", "object: station -> mean of its non-missing `humidity`", 1,
      lambda rs: {k: r2(v, 1) for k, v in by([r for r in rs if r["humidity"]], "station", _num("humidity"),
                                             statistics.mean).items()}),
    M("windiest_station", "the station with the highest mean `wind_kmh` (ties: alphabetically first)", None,
      lambda rs: argmax(by(rs, "station", _num("wind_kmh"), statistics.mean))),
    M("temp_range_by_station", "object: station -> max minus min of its non-missing `temp_c`", 1,
      lambda rs: {k: r2(max(v) - min(v), 1) for k, v in by([r for r in rs if r["temp_c"]], "station",
                                                            _num("temp_c"), list).items()}),
])


def _ffill_mean(rs):
    vals = []
    for s in sorted({r["station"] for r in rs}):
        last = None
        for r in sorted((r for r in rs if r["station"] == s), key=lambda r: r["timestamp"]):
            if r["temp_c"]:
                last = float(r["temp_c"])
            if last is not None:
                vals.append(last)
    return r2(statistics.mean(vals))


def _gaps(rs):
    out = {}
    for s in sorted({r["station"] for r in rs}):
        ts = sorted({_ts(r["timestamp"]) for r in rs if r["station"] == s})
        out[s] = int((ts[-1] - ts[0]).total_seconds() // 3600) + 1 - len(ts)
    return out


def _dupes(rs):
    seen, n = set(), 0
    for r in rs:
        k = (r["station"], r["timestamp"])
        n += k in seen
        seen.add(k)
    return n


TASKS["weather_quality"] = ("weather", WX_INTRO, [
    M("missing_pct_by_column", "object with keys `temp_c` and `humidity` -> percentage of rows where that column is "
      "empty", 2, lambda rs: {c: pct(sum(1 for r in rs if not r[c]), len(rs), 2) for c in ("temp_c", "humidity")}),
    M("duplicate_rows", "number of rows whose (`station`, `timestamp`) pair already appeared in an EARLIER row of the "
      "file", None, _dupes),
    M("stations", "alphabetically sorted list of stations", None, lambda rs: sorted({r["station"] for r in rs})),
    M("first_timestamp", "the smallest `timestamp`", None, lambda rs: min(r["timestamp"] for r in rs)),
    M("last_timestamp", "the largest `timestamp`", None, lambda rs: max(r["timestamp"] for r in rs)),
    M("missing_hours_by_station", "object: station -> number of whole hours between its first and last timestamp "
      "(inclusive) that have no row", None, _gaps),
    M("mean_temp_ffill", "mean `temp_c` over all rows after filling each missing `temp_c` with the previous "
      "non-missing value of the same station (rows of a station in timestamp order, stable sort keeping file order "
      "for equal timestamps; a station's leading missing values stay missing and are left out)", 2, _ffill_mean),
])

# ---------------------------------------------------------------- staff

STAFF_INTRO = ("`data.csv` holds one row per employee: `employee_id` (integer), `dept`, `level` (L1-L5), "
               "`location`, `hire_date` (YYYY-MM-DD), `salary` (annual, integer), `bonus_pct` (bonus as a percentage "
               "of salary) and `status` (`active` or `left`). Unless a metric says otherwise, count only ACTIVE "
               "employees.")


def _act(rs):
    return [r for r in rs if r["status"] == "active"]


def _avg_sal_dept(rs):
    return by(_act(rs), "dept", _i("salary"), statistics.mean)


TASKS["staff_headcount"] = ("staff", STAFF_INTRO, [
    M("headcount", "number of active employees", None, lambda rs: len(_act(rs))),
    M("headcount_by_dept", "object: dept -> active headcount", None, lambda rs: by(_act(rs), "dept", lambda r: 1)),
    M("avg_salary_by_dept", "object: dept -> mean salary of its active employees, rounded to a whole number "
      "(an integer)", 0, lambda rs: {k: int(round(v)) for k, v in _avg_sal_dept(rs).items()}),
    M("median_salary", "median salary of active employees (mean of the two middle values when the count is even)",
      1, lambda rs: r2(statistics.median(int(r["salary"]) for r in _act(rs)), 1)),
    M("payroll_cost", "sum over active employees of salary * (1 + bonus_pct / 100)", 2,
      lambda rs: r2(sum(int(r["salary"]) * (1 + float(r["bonus_pct"]) / 100) for r in _act(rs)))),
    M("attrition_rate_pct", "percentage of ALL rows with status `left`", 1,
      lambda rs: pct(sum(1 for r in rs if r["status"] == "left"), len(rs))),
    M("highest_paid_dept", "the dept with the highest mean active salary (unrounded; ties: alphabetically first)",
      None, lambda rs: argmax(_avg_sal_dept(rs))),
])

BANDS = [("<50k", 0, 50000), ("50k-100k", 50000, 100000), ("100k-150k", 100000, 150000), (">=150k", 150000, 10 ** 12)]

TASKS["staff_pay"] = ("staff", STAFF_INTRO, [
    M("hires_by_year", "object: year `YYYY` of `hire_date` -> number of ALL employees (active or left) hired that "
      "year", None, lambda rs: dict(sorted(Counter(r["hire_date"][:4] for r in rs).items()))),
    M("avg_tenure_years", "mean over active employees of (2024-12-31 minus `hire_date`) in days divided by 365.25", 2,
      lambda rs: r2(statistics.mean((dt.date(2024, 12, 31) - dt.date.fromisoformat(r["hire_date"])).days / 365.25
                                    for r in _act(rs)))),
    M("salary_bands", "object with exactly the keys `<50k`, `50k-100k`, `100k-150k`, `>=150k` -> active headcount "
      "whose salary is in [lower, upper): <50k is below 50000, 50k-100k is 50000 to 99999, and so on", None,
      lambda rs: {b: sum(1 for r in _act(rs) if lo <= int(r["salary"]) < hi) for b, lo, hi in BANDS}),
    M("level_share_pct", "object: level -> percentage of active employees at that level", 1,
      lambda rs: {k: pct(v, len(_act(rs))) for k, v in by(_act(rs), "level", lambda r: 1).items()}),
    M("top_earners", "list of the `employee_id`s (integers) of the 3 active employees with the highest salary, "
      "highest first (ties: smaller id first)", None,
      lambda rs: [int(r["employee_id"]) for r in sorted(_act(rs), key=lambda r: (-int(r["salary"]),
                                                                                 int(r["employee_id"])))[:3]]),
    M("salary_spread_by_dept", "object: dept -> max minus min active salary (integer)", None,
      lambda rs: {k: max(v) - min(v) for k, v in by(_act(rs), "dept", _i("salary"), list).items()}),
    M("bonus_cost", "sum over active employees of salary * bonus_pct / 100", 2,
      lambda rs: r2(sum(int(r["salary"]) * float(r["bonus_pct"]) / 100 for r in _act(rs)))),
])

TASKS["staff_locations"] = ("staff", STAFF_INTRO, [
    M("headcount_by_location", "object: location -> active headcount", None,
      lambda rs: by(_act(rs), "location", lambda r: 1)),
    M("avg_bonus_pct_by_level", "object: level -> mean `bonus_pct` of its active employees", 2,
      lambda rs: {k: r2(v) for k, v in by(_act(rs), "level", _num("bonus_pct"), statistics.mean).items()}),
    M("left_pct_by_dept", "object: dept -> percentage of ALL its rows with status `left`", 1,
      lambda rs: {k: pct(sum(1 for r in v if r["status"] == "left"), len(v))
                  for k, v in by(rs, "dept", lambda r: r, list).items()}),
    M("newest_hire", "object with keys `employee_id` (integer) and `hire_date` for the active employee with the "
      "latest `hire_date` (ties: smaller id)", None,
      lambda rs: (lambda r: {"employee_id": int(r["employee_id"]), "hire_date": r["hire_date"]})(
          sorted(_act(rs), key=lambda r: (r["hire_date"], -int(r["employee_id"])))[-1])),
    M("hired_2024_active", "number of active employees hired in 2024", None,
      lambda rs: sum(1 for r in _act(rs) if r["hire_date"][:4] == "2024")),
    M("median_salary_by_level", "object: level -> median active salary (mean of the two middle values when the "
      "count is even)", 1, lambda rs: {k: r2(v, 1) for k, v in by(_act(rs), "level", _i("salary"),
                                                                   statistics.median).items()}),
    M("remote_share_pct", "percentage of active employees whose location is `Remote`", 1,
      lambda rs: pct(sum(1 for r in _act(rs) if r["location"] == "Remote"), len(_act(rs)))),
])


def reference_report(task, path):
    _, _, metrics = TASKS[task]
    rows = load(path)
    return {m["key"]: m["fn"](rows) for m in metrics}


def task_md(task):
    dataset, intro, metrics = TASKS[task]
    lines = ["# Report", "", intro, "",
             "Write `report.py`: run as `python report.py` in this directory, it reads `data.csv` and writes "
             "`report.json` -- one JSON object with exactly the keys below (any order). It must compute everything "
             "from `data.csv` (it is also run on another file with the same columns). Standard library only.", "",
             "Rounding: a metric with \"round to N\" is rounded with Python's `round(x, N)` (for an object or list, "
             "every number in it); the others are exact integers, strings, lists or objects as described. Objects "
             "keyed by a category contain exactly the categories present in the data for that metric.", "",
             "| key | definition | rounding |", "|---|---|---|"]
    for m in metrics:
        rnd = "-" if m["dp"] is None else f"round to {m['dp']}"
        lines.append(f"| `{m['key']}` | {m['text']} | {rnd} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    build()
