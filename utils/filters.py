"""Multi-value filter helpers shared by every list endpoint."""


def split_multi(value):
    """Parse a comma-separated multi-select value into a list of strings."""
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def filter_multi(records, field, value, default=None):
    """Keep records whose `field` matches any of the comma-separated values."""
    wanted = split_multi(value)
    if not wanted:
        return records
    return [r for r in records if (r.get(field) or default or "") in wanted]


def filter_month(records, field, month):
    """Keep records whose `field` date falls in a 'YYYY-MM' month.

    Accepts several months as a comma-separated list, so the same helper
    backs both a single month picker and a multi-month selection.
    """
    months = split_multi(month)
    if not months:
        return records
    return [r for r in records
            if any((r.get(field) or "").startswith(m) for m in months)]


def filter_range(records, field, start="", end=""):
    """Keep records whose `field` (a YYYY-MM-DD string) falls in the range."""
    rows = records
    if start:
        rows = [r for r in rows if (r.get(field) or "") >= start]
    if end:
        rows = [r for r in rows if (r.get(field) or "") <= end]
    return rows


def filter_search(records, query, fields):
    """Keep records where any of `fields` contains the query (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return records
    return [r for r in records
            if q in " ".join(str(r.get(f) or "") for f in fields).lower()]
