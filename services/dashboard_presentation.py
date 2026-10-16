"""Read-only dashboard projections; established financial reports own every metric."""
from services.analytics import resolve_filters
from services.financials import financial_report


def dashboard_report(args):
    if set(args) - {'preset', 'start_date', 'end_date'}:
        raise ValueError('Dashboard supports date-range filters only.')
    filters = resolve_filters(dict(args) or {'preset': 'last7'})
    report = financial_report(filters)
    series = report['sales']['series']
    # Geometry is presentation only; currency labels retain authoritative integer cents.
    maximum = max((row['revenue_cents'] for row in series), default=0) or 1
    points = []
    for index, row in enumerate(series):
        x = 20 + index * 760 / max(1, len(series) - 1)
        y = 180 - row['revenue_cents'] * 150 / maximum
        points.append(f'{x:.2f},{y:.2f}')
    return dict(report=report, trend_points=' '.join(points), trend_max=maximum)
