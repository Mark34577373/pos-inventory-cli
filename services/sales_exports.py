"""In-memory bounded CSV downloads; no sensitive files are written to disk."""

import csv
import io

from services.money import from_cents

KINDS = ("summary", "periods", "products", "transactions")


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
                "Recorded sale totals include tax. Item sales exclude tax. No refunds, voids, or discounts supported.",
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
                ]
            )
    return output.getvalue().encode("utf-8-sig")
