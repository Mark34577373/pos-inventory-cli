"""In-memory bounded CSV downloads; no sensitive files are written to disk."""

import csv
import io

from services.money import from_cents

KINDS = ("summary", "periods", "products", "transactions", "refunds")


def spreadsheet_text(value):
    """Neutralize formulas, including whitespace/BOM-prefixed payloads."""
    value = str(value)
    stripped = value.lstrip("\ufeff \t\r\n\v\f")
    if stripped.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def amount(cents):
    return f"{from_cents(cents):.2f}"


def create_csv(data, kind):
    if kind not in KINDS:
        raise ValueError("Choose a supported sales report.")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    filters = data["filters"]
    writer.writerows(
        [
            ["TCCP simulated sales report", kind],
            ["Store ID", data["store"]["id"]],
            ["Store name", spreadsheet_text(data["store"]["name"])],
            ["Reporting timezone", data["timezone"]],
            ["Start date (inclusive)", filters.start.isoformat()],
            ["End date (inclusive)", filters.end.isoformat()],
            ["Generated at (UTC)", data["generated_at"]],
            ["Grouping", filters.group],
            ["Product ranking", filters.ranking],
            ["Product limit", filters.top],
            [
                "Financial model",
                "Gross sales include tax on sale date. Refunds include tax on refund business date. Net = gross minus refunds. Rankings remain gross before tax. No discounts or voids.",
            ],
            [
                "Tax disclosure",
                "Total minus recorded line items is tax/reconciliation difference; legacy tax may be unverified.",
            ],
            [],
        ]
    )
    summary = data["summary"]
    if kind == "summary":
        writer.writerow(["Metric", "Value"])
        writer.writerows(
            [
                ["Recorded sale totals USD (tax included)", amount(summary["revenue_cents"])],
                ["Recorded item sales USD (before tax)", amount(summary["item_sales_cents"])],
                ["Tax / reconciliation difference USD", amount(summary["tax_remainder_cents"])],
                ["Refunds USD (refund date)", amount(summary["refunded_cents"])],
                ["Net sales USD", amount(summary["net_sales_cents"])],
                ["Completed simulated refunds", summary["refund_count"]],
                ["Committed transactions", summary["transaction_count"]],
                [
                    "Average order value USD (half-up rounded)",
                    amount(summary["average_order_cents"]),
                ],
                ["Recorded units sold", summary["units_sold"]],
                ["Transactions with unverified tax", summary["unverified_tax_transactions"]],
                ["Previous period start", filters.previous.start.isoformat()],
                ["Previous period end", filters.previous.end.isoformat()],
                [
                    "Previous period recorded totals USD",
                    amount(data["comparison"]["previous_cents"]),
                ],
                ["Change USD", amount(data["comparison"]["change_cents"])],
                [
                    "Change percent",
                    str(data["comparison"]["percent"])
                    if data["comparison"]["has_baseline"]
                    else "No prior-period baseline",
                ],
            ]
        )
    elif kind == "periods":
        writer.writerow(
            [
                "Period start (inclusive)",
                "Period end (inclusive)",
                "Recorded sale totals USD (tax included)",
                "Committed transactions",
                "Average order value USD",
                "Refunds USD (refund date)",
                "Net sales USD",
            ]
        )
        for row in data["series"]:
            writer.writerow(
                [
                    row["period_start"],
                    row["period_end"],
                    amount(row["revenue_cents"]),
                    row["transaction_count"],
                    amount(row["average_order_cents"]),
                    amount(row["refunded_cents"]),
                    amount(row["net_sales_cents"]),
                ]
            )
    elif kind == "products":
        writer.writerow(
            [
                "Rank",
                "Product ID",
                "Historical product name",
                "Recorded units sold",
                "Item sales USD (before tax)",
            ]
        )
        for row in data["products"]:
            writer.writerow(
                [
                    row["rank"],
                    row["product_id"],
                    spreadsheet_text(row["product_name"]),
                    row["units_sold"],
                    amount(row["item_sales_cents"]),
                ]
            )
    elif kind == 'refunds':
        writer.writerow(['Refund ID', 'Original transaction ID', 'Recorded at UTC', 'Refund business date', 'Refund USD including tax', 'Allocated tax USD', 'Restocked'])
        for refund in data['refunds']:
            writer.writerow([refund['id'], refund['transaction_id'], refund['recorded_at'], refund['business_date'], amount(refund['total_cents']), amount(refund['tax_cents']), 'Yes' if refund['restock'] else 'No'])
    else:
        writer.writerow(
            [
                "Transaction ID",
                "Business-local timestamp",
                "Recorded payment method (simulated)",
                "Recorded sale total USD (tax included)",
                "Item sales USD (before tax)",
                "Tax / reconciliation difference USD",
                "Tax reconciled to recorded rate",
                "Cumulative refunds USD (all dates)",
            ]
        )
        for tx in data["transactions"]:
            writer.writerow(
                [
                    tx["id"],
                    tx["timestamp"],
                    spreadsheet_text(tx["payment_method"]),
                    amount(tx["total_cents"]),
                    amount(tx["item_sales_cents"]),
                    amount(tx["tax_remainder_cents"]),
                    "Yes" if tx["tax_verified"] else "Unverified",
                    amount(tx["refunded_cents"]),
                ]
            )
    return output.getvalue().encode("utf-8-sig")
