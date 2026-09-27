"""Tests for KPI calculations, aggregations and generated insights."""

import pandas as pd
import pytest

from analysis import sales_analysis as sa
from utils.data_processing import clean_sales_data, load_sample_data


@pytest.fixture
def sales():
    raw = pd.DataFrame(
        {
            "Order ID": ["A1", "A1", "A2", "A3", "A4", "A5"],
            "Order Date": ["2024-01-05", "2024-01-05", "2024-01-20", "2024-03-02", "2024-03-18", "2024-04-30"],
            "Product": ["Mug", "Kettle", "Mug", "Lamp", "Kettle", "Lamp"],
            "Category": ["Kitchen", "Kitchen", "Kitchen", "Home", "Kitchen", "Home"],
            "Region": ["North", "North", "South", "West", "South", "West"],
            "Quantity": [2, 1, 3, 1, 2, 1],
            "Unit Price": [10.0, 30.0, 10.0, 45.0, 30.0, 45.0],
        }
    )
    cleaned, _ = clean_sales_data(raw)
    return cleaned


def test_kpis(sales):
    kpis = sa.calculate_kpis(sales)
    assert kpis["total_revenue"] == pytest.approx(230.0)
    assert kpis["total_orders"] == 5  # A1 has two lines but is one order
    assert kpis["units_sold"] == 10
    assert kpis["average_order_value"] == pytest.approx(46.0)


def test_kpis_on_empty_data(sales):
    kpis = sa.calculate_kpis(sales.iloc[0:0])
    assert kpis == {"total_revenue": 0.0, "total_orders": 0, "units_sold": 0.0, "average_order_value": 0.0}


def test_monthly_sales_fills_missing_months(sales):
    monthly = sa.monthly_sales(sales)
    assert monthly["month"].dt.strftime("%Y-%m").tolist() == ["2024-01", "2024-02", "2024-03", "2024-04"]
    assert monthly["revenue"].tolist() == [80.0, 0.0, 105.0, 45.0]
    assert monthly["orders"].tolist() == [2, 0, 2, 1]


@pytest.mark.parametrize("frequency", ["D", "W", "M"])
def test_sales_over_time_totals_match(sales, frequency):
    over_time = sa.sales_over_time(sales, frequency)
    assert over_time["revenue"].sum() == pytest.approx(230.0)


def test_sales_by_category_sorted_with_shares(sales):
    categories = sa.category_sales(sales)
    assert categories["category"].tolist() == ["Kitchen", "Home"]
    assert categories["revenue"].tolist() == [140.0, 90.0]
    assert categories["revenue_share"].sum() == pytest.approx(1.0)


def test_region_sales(sales):
    regions = sa.region_sales(sales).set_index("region")
    assert regions.loc["South", "revenue"] == 90.0
    assert regions.loc["South", "orders"] == 2


def test_top_products_by_revenue_and_units(sales):
    assert sa.top_products(sales, "revenue")["product"].tolist() == ["Kettle", "Lamp", "Mug"]
    assert sa.top_products(sales, "units", n=1)["product"].tolist() == ["Mug"]
    with pytest.raises(ValueError):
        sa.top_products(sales, "profit")


def test_percent_change():
    assert sa.percent_change(120, 100) == pytest.approx(0.2)
    assert sa.percent_change(50, 0) is None


def test_insights_are_computed_from_data(sales):
    insights = {insight.title: insight for insight in sa.generate_insights(sales)}
    assert "**Kitchen**" in insights["Highest-revenue category"].message
    assert "**Kettle**" in insights["Top-performing product"].message
    assert "**March 2024**" in insights["Strongest sales month"].message
    # March ($105) -> April ($45) is a decline of 57.1%.
    month_over_month = insights["Month-over-month change"]
    assert month_over_month.tone == "negative"
    assert "-57.1%" in month_over_month.message
    # Only four months of data – not enough for the six-month comparison.
    assert insights["Recent sales momentum"].tone == "unavailable"


def test_partial_latest_month_is_excluded_from_growth(sales):
    # With the range ending mid-April, April is incomplete; compare Feb -> Mar instead.
    subset = sales[sales["order_date"] <= "2024-04-15"]
    insights = {i.title: i for i in sa.generate_insights(subset, end_date="2024-04-15")}
    message = insights["Month-over-month change"].message
    assert "March 2024" in message
    # February had zero revenue, so no percentage can be given.
    assert insights["Month-over-month change"].tone == "unavailable"


def test_insights_for_empty_and_tiny_selections(sales):
    empty = sa.generate_insights(sales.iloc[0:0])
    assert len(empty) == 1 and empty[0].tone == "unavailable"

    single_row = sa.generate_insights(sales.iloc[[0]])
    tones = {insight.title: insight.tone for insight in single_row}
    assert tones["Strongest sales month"] == "unavailable"
    assert tones["Month-over-month change"] == "unavailable"
    assert tones["Revenue concentration"] == "unavailable"


def test_sample_data_kpis_match_raw_csv():
    """KPIs from the cleaned sample must equal simple sums over the raw file."""
    sample, _ = load_sample_data()
    raw = pd.read_csv("data/sales_data.csv")
    kpis = sa.calculate_kpis(sample)
    assert kpis["total_revenue"] == pytest.approx(raw["Revenue"].sum())
    assert kpis["total_orders"] == raw["Order ID"].nunique()
    assert kpis["units_sold"] == raw["Quantity"].sum()
    insights = sa.generate_insights(sample)
    assert all(insight.tone != "unavailable" for insight in insights)
