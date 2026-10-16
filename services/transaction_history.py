"""Bounded, allowlisted transaction browsing at the authorized data boundary."""

from contextlib import closing

from database.database import get_store_connection
from services.analytics import ReportLimitError, strict_date

SORTS = {
    "newest": "t.timestamp DESC,t.id DESC",
    "oldest": "t.timestamp,t.id",
    "total_desc": "t.total_cents DESC,t.id DESC",
    "total_asc": "t.total_cents,t.id",
}
STATUSES = ("", "completed", "partial", "refunded")


def query_transactions(args):
    q = args.get("q", "").strip()
    payment = args.get("payment_method", "").strip().title()
    status = args.get("status", "")
    sort = args.get("sort", "newest")
    page_text = str(args.get("page", "1"))
    if (
        len(q) > 100
        or payment not in ("", "Cash", "Card")
        or status not in STATUSES
        or sort not in SORTS
        or not page_text.isascii()
        or not page_text.isdigit()
        or len(page_text) > 5
        or not 1 <= int(page_text) <= 10000
    ):
        raise ValueError("Choose valid transaction filters.")
    start = args.get("start_date", "")
    end = args.get("end_date", "")
    if start:
        strict_date(start)
    if end:
        strict_date(end)
    if start and end and start > end:
        raise ValueError("Start date cannot be later than end date.")
    conditions = ["t.store_id=authorized_store_id()"]
    params = []
    if q:
        if q.isascii() and q.isdecimal():
            conditions.append("t.id=?")
            params.append(int(q) if len(q) <= 19 and int(q) <= 9223372036854775807 else -1)
        else:
            conditions.append("(CAST(t.id AS TEXT) LIKE ? OR t.timestamp LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
    for condition, value in [
        ("t.payment_method=?", payment),
        ("substr(t.timestamp,1,10)>=?", start),
        ("substr(t.timestamp,1,10)<=?", end),
    ]:
        if value:
            conditions.append(condition)
            params.append(value)
    base = """SELECT t.id,t.timestamp,t.total_cents,t.payment_method,
        COALESCE((SELECT SUM(r.total_cents) FROM refunds r WHERE r.store_id=t.store_id AND r.transaction_id=t.id),0) AS refunded_cents,
        CASE WHEN NOT EXISTS(SELECT 1 FROM refunds r WHERE r.store_id=t.store_id AND r.transaction_id=t.id) THEN 'completed'
        WHEN (SELECT SUM(r.total_cents) FROM refunds r WHERE r.store_id=t.store_id AND r.transaction_id=t.id)=t.total_cents THEN 'refunded' ELSE 'partial' END AS status
        FROM transactions t WHERE """ + " AND ".join(conditions)
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        query = f'SELECT * FROM ({base}) WHERE (?="" OR status=?)'
        bindings = [*params, status, status]
        totals = conn.execute(query + " LIMIT 10001", bindings).fetchall()
        if len(totals) > 10000:
            raise ReportLimitError("Narrow transaction filters to at most 10,000 matching sales.")
        count = len(totals)
        pages = max(1, (count + 19) // 20)
        page = min(int(page_text), pages)
        # SQL sort expressions are internal constants; outer aliases omit t.
        order = SORTS[sort].replace("t.", "")
        rows = [
            dict(row)
            for row in conn.execute(
                query + f" ORDER BY {order} LIMIT 20 OFFSET ?", [*bindings, (page - 1) * 20]
            )
        ]
    return dict(
        transactions=rows,
        transaction_count=count,
        total_cents=sum(row["total_cents"] for row in totals),
        page=page,
        pages=pages,
        search_text=q,
        payment_method=payment,
        start_date=start,
        end_date=end,
        status=status,
        sort=sort,
    )
