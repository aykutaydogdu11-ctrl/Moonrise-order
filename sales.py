"""
sales.py
============================================================
Persists completed sales (not stock — units sold) so staff can
see turnover and item counts over a period, without needing a
separate stock-counting system. A "sale" is recorded when staff
tap "Sale Kaydet" on a priced order; it does NOT track ingredient
stock levels, only what was sold and for how much.
============================================================
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone


SALES_LOG_FILE = os.environ.get("SALES_LOG_FILE", "sales_log.json")


def load_sales():
    try:
        with open(SALES_LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_sales(records):
    with open(SALES_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def record_sale(table, sale_items, total):
    """
    sale_items: list of {"name","qty","unit_price","line_total"}
    as produced by pricing.apply_pricing(). Appends one record —
    does not touch existing history.
    """

    records = load_sales()

    records.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "table": table or "",
        "items": sale_items,
        "total": round(total, 2)
    })

    _save_sales(records)
    return records[-1]


def delete_last_sale():
    """Undo the most recently recorded sale (for a misclick)."""

    records = load_sales()

    if not records:
        return None

    removed = records.pop()
    _save_sales(records)
    return removed


def clear_sales():
    _save_sales([])


def _parse_ts(record):
    try:
        return datetime.fromisoformat(record["timestamp"])
    except (KeyError, ValueError):
        return None


def filter_since(records, cutoff):
    if cutoff is None:
        return records

    return [
        r for r in records
        if (ts := _parse_ts(r)) is not None and ts >= cutoff
    ]


def summarize(records):
    """
    Returns {"item_counts": [(name, qty, revenue), ...] sorted by
    qty desc, "revenue": total, "order_count": n}.
    """

    item_counts = {}
    revenue = 0.0

    for record in records:
        revenue += record.get("total", 0.0)

        for item in record.get("items", []):
            name = item.get("name", "?")
            qty = item.get("qty", 1)
            line_total = item.get("line_total", 0.0)

            if name not in item_counts:
                item_counts[name] = {"qty": 0, "revenue": 0.0}

            item_counts[name]["qty"] += qty
            item_counts[name]["revenue"] += line_total

    sorted_items = sorted(
        (
            (name, data["qty"], data["revenue"])
            for name, data in item_counts.items()
        ),
        key=lambda t: t[1],
        reverse=True
    )

    return {
        "item_counts": sorted_items,
        "revenue": revenue,
        "order_count": len(records)
    }


def today_cutoff():
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def week_cutoff():
    return today_cutoff() - timedelta(days=today_cutoff().weekday())


def build_report():
    """
    Returns {"today": summary, "week": summary, "all_time": summary}
    ready for the report template.
    """

    records = load_sales()

    return {
        "today": summarize(filter_since(records, today_cutoff())),
        "week": summarize(filter_since(records, week_cutoff())),
        "all_time": summarize(records),
        "raw_count": len(records)
    }
