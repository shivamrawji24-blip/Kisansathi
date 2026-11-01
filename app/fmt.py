"""Small formatting helpers shared by the engines (Indian digit grouping)."""
from __future__ import annotations


def inr(amount: float | None) -> str:
    """12345678 -> '₹1,23,45,678' (Indian digit grouping)."""
    if amount is None:
        return "—"
    neg = amount < 0
    n = int(round(abs(amount)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + "₹" + s


def merge_meta(metas: list[dict | None]) -> list[dict]:
    """Collapse many per-call meta blocks into one entry per data source.

    stale -> True if *any* call for that source fell back to saved data;
    fetched_at -> the oldest timestamp among them (the honest freshness)."""
    merged: dict[str, dict] = {}
    for m in metas:
        if not m:
            continue
        cur = merged.setdefault(m["source"], {"source": m["source"], "stale": False,
                                              "fetched_at": m.get("fetched_at"), "error": None})
        cur["stale"] = cur["stale"] or bool(m.get("stale"))
        if m.get("error"):
            cur["error"] = m["error"]
        a, b = cur["fetched_at"], m.get("fetched_at")
        if a is None or b is None:
            cur["fetched_at"] = a if b is None else b if a is None else None
        else:
            cur["fetched_at"] = min(a, b)
    return list(merged.values())
