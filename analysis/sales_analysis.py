"""Sales metrics, aggregations and rule-based business insights.

All functions expect a DataFrame produced by
``utils.data_processing.clean_sales_data`` (canonical column names) and return
plain numbers or DataFrames. Nothing here is hard-coded: every figure is
computed from the rows passed in, so the same functions power the KPIs, the
charts and the insights for any filter selection.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.formatting import format_currency, format_number, format_percent

# Period codes accepted by ``sales_over_time``.
FREQUENCIES = {"Daily": "D", "Weekly": "W", "Monthly": "M"}


# ---------------------------------------------------------------------------
# Headline KPIs
# ---------------------------------------------------------------------------


def total_revenue(df: pd.DataFrame) -> float:
    """Sum of revenue across all order lines."""
    return float(df["revenue"].sum()) if not df.empty else 0.0


def total_orders(df: pd.DataFrame) -> int:
    """Number of distinct orders (an order can contain several product lines)."""
    return int(df["order_id"].nunique()) if not df.empty else 0


def units_sold(df: pd.DataFrame) -> float:
    """Total quantity of items sold."""
    return float(df["quantity"].sum()) if not df.empty else 0.0


def average_order_value(df: pd.DataFrame) -> float:
    """Revenue divided by number of orders (0 when there are no orders)."""
    orders = total_orders(df)
    return total_revenue(df) / orders if orders else 0.0


def calculate_kpis(df: pd.DataFrame) -> dict[str, float]:
    """All headline metrics in one dictionary."""
    return {
        "total_revenue": total_revenue(df),
        "total_orders": total_orders(df),
        "units_sold": units_sold(df),
        "average_order_value": average_order_value(df),
    }


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

AGGREGATE_COLUMNS = ["revenue", "orders", "units", "average_order_value"]


def _aggregate(grouped) -> pd.DataFrame:
    """Shared revenue / orders / units / AOV aggregation for any groupby."""
    summary = grouped.agg(
        revenue=("revenue", "sum"),
        orders=("order_id", "nunique"),
        units=("quantity", "sum"),
    )
    summary["average_order_value"] = (summary["revenue"] / summary["orders"]).where(summary["orders"] > 0, 0.0)
    return summary


def sales_over_time(df: pd.DataFrame, frequency: str = "M") -> pd.DataFrame:
    """Revenue, orders, units and AOV per day ('D'), week ('W') or month ('M').

    Periods with no sales inside the data's date span are included with zeros,
    so trend lines don't skip gaps. The ``period`` column holds the start date
    of each period.
    """
    if df.empty:
        return pd.DataFrame(columns=["period", *AGGREGATE_COLUMNS])

    periods = df["order_date"].dt.to_period(frequency)
    summary = _aggregate(df.groupby(periods))
    full_range = pd.period_range(periods.min(), periods.max(), freq=frequency)
    summary = summary.reindex(full_range, fill_value=0)
    summary.index = summary.index.start_time
    summary.index.name = "period"
    return summary.reset_index()


def monthly_sales(df: pd.DataFrame) -> pd.DataFrame:
    """Sales totals per calendar month (see ``sales_over_time``)."""
    return sales_over_time(df, "M").rename(columns={"period": "month"})


def sales_by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Totals per value of ``column`` (e.g. 'category'), sorted by revenue.

    Adds ``revenue_share`` – each group's fraction of total revenue.
    """
    if df.empty:
        return pd.DataFrame(columns=[column, *AGGREGATE_COLUMNS, "revenue_share"])
    summary = _aggregate(df.groupby(column, observed=True)).sort_values("revenue", ascending=False)
    revenue = summary["revenue"].sum()
    summary["revenue_share"] = summary["revenue"] / revenue if revenue else 0.0
    return summary.reset_index()


def category_sales(df: pd.DataFrame) -> pd.DataFrame:
    return sales_by(df, "category")


def region_sales(df: pd.DataFrame) -> pd.DataFrame:
    return sales_by(df, "region")


def product_sales(df: pd.DataFrame) -> pd.DataFrame:
    """Per-product totals, including the product's category."""
    if df.empty:
        return pd.DataFrame(columns=["product", "category", *AGGREGATE_COLUMNS, "revenue_share"])
    summary = sales_by(df, "product")
    # A product normally belongs to one category; use the most common one.
    main_category = df.groupby("product", observed=True)["category"].agg(lambda values: values.mode().iat[0])
    summary.insert(1, "category", summary["product"].map(main_category))
    return summary


def top_products(df: pd.DataFrame, by: str = "revenue", n: int = 10) -> pd.DataFrame:
    """The ``n`` best products ranked by 'revenue' or 'units'."""
    if by not in ("revenue", "units"):
        raise ValueError("`by` must be 'revenue' or 'units'")
    products = product_sales(df)
    return products.sort_values([by, "product"], ascending=[False, True]).head(n).reset_index(drop=True)


def percent_change(current: float, previous: float) -> float | None:
    """Relative change from ``previous`` to ``current``; None when undefined."""
    if previous == 0:
        return None
    return (current - previous) / previous


# ---------------------------------------------------------------------------
# Business insights
# ---------------------------------------------------------------------------


@dataclass
class Insight:
    """One rule-based finding. ``tone`` drives the icon shown in the UI."""

    title: str
    message: str
    tone: str = "neutral"  # "positive", "negative", "neutral" or "unavailable"


def _complete_months(monthly: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    """Keep only months that lie entirely inside [start_date, end_date]."""
    month_start = monthly["month"]
    month_end = month_start + pd.offsets.MonthEnd(0)
    inside = (month_start >= pd.Timestamp(start_date)) & (month_end <= pd.Timestamp(end_date))
    return monthly.loc[inside].reset_index(drop=True)


def _dimension_insight(df: pd.DataFrame, column: str, label: str, plural: str) -> Insight:
    summary = sales_by(df, column)
    title = f"Highest-revenue {label}"
    if summary.empty or summary["revenue"].sum() == 0:
        return Insight(title, f"Not enough revenue data to compare {plural}.", "unavailable")
    leader = summary.iloc[0]
    if len(summary) == 1:
        return Insight(
            title,
            f"Only one {label} (**{leader[column]}**) is in the current selection, "
            f"generating {format_currency(leader['revenue'])}.",
        )
    runner_up = summary.iloc[1]
    return Insight(
        title,
        f"**{leader[column]}** leads with {format_currency(leader['revenue'])} "
        f"({format_percent(leader['revenue_share'])} of revenue), ahead of "
        f"{runner_up[column]} at {format_currency(runner_up['revenue'])} "
        f"({format_percent(runner_up['revenue_share'])}).",
    )


def _product_insight(df: pd.DataFrame) -> Insight:
    title = "Top-performing product"
    products = product_sales(df)
    if products.empty or products["revenue"].sum() == 0:
        return Insight(title, "Not enough data to rank products.", "unavailable")
    by_revenue = top_products(df, "revenue", 1).iloc[0]
    by_units = top_products(df, "units", 1).iloc[0]
    message = (
        f"**{by_revenue['product']}** ({by_revenue['category']}) generated the most revenue: "
        f"{format_currency(by_revenue['revenue'])} from {format_number(by_revenue['units'])} units."
    )
    if by_units["product"] != by_revenue["product"]:
        message += (
            f" The most units were sold of **{by_units['product']}** "
            f"({format_number(by_units['units'])} units)."
        )
    return Insight(title, message)


def _best_month_insight(monthly: pd.DataFrame) -> Insight:
    title = "Strongest sales month"
    active = monthly.loc[monthly["revenue"] > 0]
    if len(active) < 2:
        return Insight(title, "At least two months of sales are needed to compare months.", "unavailable")
    best = active.loc[active["revenue"].idxmax()]
    average = active["revenue"].mean()
    return Insight(
        title,
        f"**{best['month']:%B %Y}** was the best month with {format_currency(best['revenue'])} "
        f"from {format_number(best['orders'])} orders – {format_percent(best['revenue'] / average - 1, signed=True)} "
        f"versus the average month ({format_currency(average)}).",
    )


def _month_over_month_insight(complete: pd.DataFrame, excluded_partial: bool) -> Insight:
    title = "Month-over-month change"
    if len(complete) < 2:
        return Insight(
            title,
            "At least two complete months are needed to measure month-over-month growth. "
            "Try widening the date range.",
            "unavailable",
        )
    latest, previous = complete.iloc[-1], complete.iloc[-2]
    change = percent_change(latest["revenue"], previous["revenue"])
    note = " (the latest, partial month is excluded)" if excluded_partial else ""
    if change is None:
        return Insight(
            title,
            f"{previous['month']:%B %Y} had no revenue, so growth into {latest['month']:%B %Y} "
            f"({format_currency(latest['revenue'])}) cannot be expressed as a percentage.",
            "unavailable",
        )
    direction = "grew" if change > 0 else "declined" if change < 0 else "was flat"
    tone = "positive" if change > 0 else "negative" if change < 0 else "neutral"
    order_change = percent_change(latest["orders"], previous["orders"])
    orders_text = (
        f" Orders changed {format_percent(order_change, signed=True)} "
        f"({format_number(previous['orders'])} → {format_number(latest['orders'])})."
        if order_change is not None
        else ""
    )
    return Insight(
        title,
        f"Revenue {direction} **{format_percent(change, signed=True)}** from {previous['month']:%B %Y} "
        f"({format_currency(previous['revenue'])}) to {latest['month']:%B %Y} "
        f"({format_currency(latest['revenue'])}){note}.{orders_text}",
        tone,
    )


def _momentum_insight(complete: pd.DataFrame) -> Insight:
    title = "Recent sales momentum"
    if len(complete) < 6:
        return Insight(
            title,
            "At least six complete months are needed to compare the last three months with the three before.",
            "unavailable",
        )
    recent = complete.iloc[-3:]
    prior = complete.iloc[-6:-3]
    change = percent_change(recent["revenue"].sum(), prior["revenue"].sum())
    if change is None:
        return Insight(title, "The comparison period had no revenue, so momentum cannot be measured.", "unavailable")
    tone = "positive" if change > 0 else "negative" if change < 0 else "neutral"
    trend = "up" if change > 0 else "down" if change < 0 else "unchanged"
    return Insight(
        title,
        f"Revenue for {recent['month'].iloc[0]:%b %Y}–{recent['month'].iloc[-1]:%b %Y} was "
        f"{format_currency(recent['revenue'].sum())}, **{trend} {format_percent(abs(change))}** versus "
        f"{prior['month'].iloc[0]:%b %Y}–{prior['month'].iloc[-1]:%b %Y} "
        f"({format_currency(prior['revenue'].sum())}).",
        tone,
    )


def _concentration_insight(df: pd.DataFrame) -> Insight:
    title = "Revenue concentration"
    products = product_sales(df)
    if len(products) < 4 or products["revenue"].sum() == 0:
        return Insight(title, "At least four products are needed to measure revenue concentration.", "unavailable")
    top_three_share = products["revenue"].head(3).sum() / products["revenue"].sum()
    level = "highly concentrated" if top_three_share >= 0.5 else "fairly diversified"
    return Insight(
        title,
        f"The top 3 of {len(products)} products account for **{format_percent(top_three_share)}** of revenue – "
        f"sales are {level} across the product range.",
    )


def generate_insights(df: pd.DataFrame, start_date=None, end_date=None) -> list[Insight]:
    """Build the list of business insights for the given (filtered) data.

    ``start_date``/``end_date`` describe the selected date range; they are used
    to decide which months are complete (a half-finished month would distort
    growth figures). They default to the first and last order dates.
    """
    if df.empty:
        return [Insight("No data", "There are no sales in the current selection, so no insights can be generated.", "unavailable")]

    start_date = pd.Timestamp(start_date) if start_date is not None else df["order_date"].min().replace(day=1)
    end_date = pd.Timestamp(end_date) if end_date is not None else df["order_date"].max()

    monthly = monthly_sales(df)
    complete = _complete_months(monthly, start_date, end_date)
    excluded_partial = not complete.empty and complete["month"].iloc[-1] < monthly["month"].iloc[-1]

    return [
        _dimension_insight(df, "category", "category", "categories"),
        _product_insight(df),
        _dimension_insight(df, "region", "region", "regions"),
        _best_month_insight(monthly),
        _month_over_month_insight(complete, excluded_partial),
        _momentum_insight(complete),
        _concentration_insight(df),
    ]
