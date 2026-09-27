"""Business Insights – E-commerce Sales Analytics Dashboard.

Run with:
    streamlit run app.py

The UI lives in this file; data cleaning is in ``utils/data_processing.py``,
metrics and insights in ``analysis/sales_analysis.py`` and chart drawing in
``utils/charts.py``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from analysis import sales_analysis as sa
from utils import charts
from utils.data_processing import (
    SAMPLE_DATA_PATH,
    CleaningReport,
    DataValidationError,
    filter_sales_data,
    load_sales_data,
    load_sample_data,
    to_csv_bytes,
    to_display_columns,
)
from utils.formatting import format_currency, format_number

st.set_page_config(
    page_title="Business Insights | Sales Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

FILTER_KEYS = {
    "dates": "filter_dates",
    "categories": "filter_categories",
    "products": "filter_products",
    "regions": "filter_regions",
}
INSIGHT_ICONS = {"positive": "📈", "negative": "📉", "neutral": "💡", "unavailable": "ℹ️"}
TOP_N = 10


def escape_dollars(text: str) -> str:
    """Stop Streamlit markdown from treating "$...$" as a LaTeX formula."""
    return text.replace("$", "\\$")


# ---------------------------------------------------------------------------
# Data loading (cached so filters stay fast)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Loading sample data…")
def get_sample_data() -> tuple[pd.DataFrame, CleaningReport]:
    return load_sample_data()


@st.cache_data(show_spinner="Reading and cleaning your file…", max_entries=5)
def get_uploaded_data(file_bytes: bytes) -> tuple[pd.DataFrame, CleaningReport]:
    return load_sales_data(file_bytes)


def load_data() -> tuple[pd.DataFrame, CleaningReport, str, str]:
    """Return (data, cleaning report, source label, dataset key) from the sidebar's data section."""
    st.sidebar.header("📁 Data source")
    uploaded_file = st.sidebar.file_uploader(
        "Upload your own sales CSV",
        type=["csv"],
        help="Expected columns: Order ID, Order Date, Product, Category, Region, Quantity, "
        "Unit Price, Revenue. See the README for details.",
    )
    st.sidebar.download_button(
        "Download sample CSV (format template)",
        data=SAMPLE_DATA_PATH.read_bytes() if SAMPLE_DATA_PATH.exists() else b"",
        file_name="sample_sales_data.csv",
        mime="text/csv",
        disabled=not SAMPLE_DATA_PATH.exists(),
    )

    if uploaded_file is not None:
        try:
            data, report = get_uploaded_data(uploaded_file.getvalue())
            st.sidebar.success(escape_dollars(f"Using **{uploaded_file.name}**"))
            return data, report, f"Uploaded file: {uploaded_file.name}", f"upload:{uploaded_file.file_id}"
        except DataValidationError as error:
            st.error(escape_dollars(f"**We couldn't use “{uploaded_file.name}”.** {error}"))
        except Exception as error:  # unexpected problem – never crash the dashboard
            st.error(
                escape_dollars(f"**We couldn't use “{uploaded_file.name}”.** Unexpected error while reading the file: {error}")
            )
        st.info("Showing the built-in sample dataset instead.")

    data, report = get_sample_data()
    st.sidebar.caption("Currently showing the **synthetic demo dataset**.")
    return data, report, "Synthetic demo dataset (data/sales_data.csv)", "sample"


def show_cleaning_report(report: CleaningReport) -> None:
    lines = report.summary_lines()
    with st.sidebar.expander("🧹 Data quality report", expanded=False):
        st.markdown(
            f"**{format_number(report.rows_kept)}** of {format_number(report.rows_received)} rows are usable."
        )
        if lines:
            st.markdown("\n".join(f"- {line}" for line in lines))
        else:
            st.markdown("No problems found – no rows were changed or removed.")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def filter_key(name: str) -> str:
    """Widget key for a filter, namespaced by dataset.

    Each dataset gets its own set of filter widgets, so switching files can
    never carry a stale selection (e.g. an old date range) over to new data.
    """
    dataset_key = st.session_state.get("dataset_key", "sample")
    base = FILTER_KEYS[name]
    return base if dataset_key == "sample" else f"{base}__{dataset_key}"


def default_filters(data: pd.DataFrame) -> dict:
    return {
        filter_key("dates"): (data["order_date"].min().date(), data["order_date"].max().date()),
        filter_key("categories"): [],
        filter_key("products"): [],
        filter_key("regions"): [],
    }


def reset_filters(data: pd.DataFrame) -> None:
    """Button callback: put every filter back to "everything"."""
    for key, value in default_filters(data).items():
        st.session_state[key] = value


def prepare_filter_state(data: pd.DataFrame, dataset_key: str) -> None:
    """Initialize filters whenever a dataset is loaded for the first time."""
    if st.session_state.get("dataset_key") != dataset_key:
        st.session_state["dataset_key"] = dataset_key
        reset_filters(data)


def sidebar_filters(data: pd.DataFrame) -> dict:
    """Draw the filter widgets and return the current selection."""
    st.sidebar.header("🔎 Filters")
    min_date, max_date = data["order_date"].min().date(), data["order_date"].max().date()

    selected_dates = st.sidebar.date_input(
        "Date range",
        min_value=min_date,
        max_value=max_date,
        key=filter_key("dates"),
        help="Pick a start and an end date.",
    )
    # While a user is mid-selection the widget returns only a start date.
    if isinstance(selected_dates, (tuple, list)):
        start_date = selected_dates[0] if len(selected_dates) > 0 else min_date
        end_date = selected_dates[1] if len(selected_dates) > 1 else max_date
    else:
        start_date, end_date = selected_dates or min_date, max_date

    categories = sorted(data["category"].unique())
    selected_categories = st.sidebar.multiselect(
        "Category", categories, key=filter_key("categories"), placeholder="All categories"
    )

    # Only offer products from the chosen categories; drop choices that no longer apply.
    product_pool = data if not selected_categories else data[data["category"].isin(selected_categories)]
    products = sorted(product_pool["product"].unique())
    st.session_state[filter_key("products")] = [
        product for product in st.session_state.get(filter_key("products"), []) if product in products
    ]
    selected_products = st.sidebar.multiselect(
        "Product", products, key=filter_key("products"), placeholder="All products"
    )

    regions = sorted(data["region"].unique())
    selected_regions = st.sidebar.multiselect(
        "Region", regions, key=filter_key("regions"), placeholder="All regions"
    )

    st.sidebar.button(
        "↺ Reset filters",
        on_click=reset_filters,
        args=(data,),
        width="stretch",
        key="reset_filters_sidebar",
    )
    return {
        "start_date": start_date,
        "end_date": end_date,
        "categories": selected_categories,
        "products": selected_products,
        "regions": selected_regions,
    }


def describe_filters(filters: dict) -> str:
    parts = [f"{filters['start_date']:%d %b %Y} – {filters['end_date']:%d %b %Y}"]
    for key in ("categories", "products", "regions"):
        if filters[key]:
            count = len(filters[key])
            parts.append(", ".join(filters[key]) if count <= 2 else f"{count} {key}")
    return escape_dollars(" · ".join(parts))


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------


def show_header(source_label: str, data: pd.DataFrame) -> None:
    st.title("📊 Business Insights")
    st.markdown(
        "#### E-commerce Sales Analytics Dashboard\n"
        "Explore revenue, orders and product performance. Use the filters in the sidebar to "
        "slice the data – every KPI, chart and insight updates instantly."
    )
    st.caption(
        f"**Data source:** {escape_dollars(source_label)} · {format_number(len(data))} order lines · "
        f"{data['order_date'].min():%b %Y} – {data['order_date'].max():%b %Y}"
    )


def show_kpis(filtered: pd.DataFrame) -> None:
    kpis = sa.calculate_kpis(filtered)
    columns = st.columns(4)
    cards = [
        ("Total Revenue", format_currency(kpis["total_revenue"]), "Sum of revenue for all order lines."),
        ("Total Orders", format_number(kpis["total_orders"]), "Number of distinct order IDs."),
        ("Units Sold", format_number(kpis["units_sold"]), "Total quantity of items sold."),
        ("Average Order Value", format_currency(kpis["average_order_value"]), "Total revenue ÷ total orders."),
    ]
    for column, (label, value, help_text) in zip(columns, cards):
        with column.container(border=True):
            st.metric(label, value, help=help_text)


def show_insights(filtered: pd.DataFrame, filters: dict, data: pd.DataFrame) -> None:
    st.subheader("💡 Business Insights")
    st.caption("Generated automatically from the filtered data using simple, transparent rules.")
    # If the range starts at the first order date we treat the first month as complete.
    start = None if filters["start_date"] <= data["order_date"].min().date() else filters["start_date"]
    insights = sa.generate_insights(filtered, start_date=start, end_date=filters["end_date"])
    columns = st.columns(2)
    for index, insight in enumerate(insights):
        with columns[index % 2].container(border=True):
            st.markdown(f"**{INSIGHT_ICONS.get(insight.tone, '💡')} {insight.title}**")
            if insight.tone == "unavailable":
                st.caption(escape_dollars(insight.message))
            else:
                st.markdown(escape_dollars(insight.message))


def show_figure(fig) -> None:
    st.pyplot(fig, clear_figure=False)
    charts.close_figure(fig)


def money_table(frame: pd.DataFrame, label_column: str | None = None) -> None:
    """Show an aggregated table with friendly headers and formatting."""
    config = {
        "revenue": st.column_config.NumberColumn("Revenue", format="dollar"),
        "orders": st.column_config.NumberColumn("Orders", format="%d"),
        "units": st.column_config.NumberColumn("Units", format="localized"),
        "average_order_value": st.column_config.NumberColumn("Avg. order value", format="dollar"),
        "revenue_share": st.column_config.ProgressColumn("Revenue share", format="percent", min_value=0, max_value=1),
    }
    if label_column:
        config[label_column] = st.column_config.TextColumn(label_column.replace("_", " ").title())
    if "month" in frame.columns:
        config["month"] = st.column_config.DateColumn("Month", format="MMM YYYY")
    if "period" in frame.columns:
        config["period"] = st.column_config.DateColumn("Period start", format="YYYY-MM-DD")
    if "category" in frame.columns and label_column != "category":
        config["category"] = st.column_config.TextColumn("Category")
    st.dataframe(frame, column_config=config, hide_index=True)


def trends_tab(filtered: pd.DataFrame) -> None:
    monthly = sa.monthly_sales(filtered)
    left, right = st.columns(2)
    with left:
        show_figure(charts.line_chart(monthly, "month", "revenue", "Monthly Revenue Trend", "Revenue"))
    with right:
        show_figure(
            charts.line_chart(
                monthly, "month", "orders", "Monthly Orders Trend", "Orders",
                color=charts.COLORS["secondary"], currency=False,
            )
        )
    with st.expander("View monthly figures"):
        money_table(monthly)

    st.markdown("##### Revenue & orders over time")
    granularity = st.radio(
        "Time granularity", list(sa.FREQUENCIES), index=1, horizontal=True, key="trend_granularity"
    )
    over_time = sa.sales_over_time(filtered, sa.FREQUENCIES[granularity])
    show_figure(charts.revenue_orders_trend(over_time, f"{granularity} Revenue and Orders"))


def products_tab(filtered: pd.DataFrame) -> None:
    by_revenue = sa.top_products(filtered, "revenue", TOP_N)
    by_units = sa.top_products(filtered, "units", TOP_N)
    left, right = st.columns(2)
    with left:
        show_figure(
            charts.horizontal_bar_chart(
                by_revenue, "product", "revenue", f"Top {min(TOP_N, len(by_revenue))} Products by Revenue", "Revenue"
            )
        )
    with right:
        show_figure(
            charts.horizontal_bar_chart(
                by_units, "product", "units", f"Top {min(TOP_N, len(by_units))} Products by Units Sold",
                "Units sold", color=charts.COLORS["secondary"], currency=False,
            )
        )
    with st.expander("View all products"):
        money_table(sa.product_sales(filtered), "product")


def segments_tab(filtered: pd.DataFrame) -> None:
    categories = sa.category_sales(filtered)
    regions = sa.region_sales(filtered)
    left, right = st.columns(2)
    with left:
        show_figure(
            charts.horizontal_bar_chart(
                categories, "category", "revenue", "Revenue by Category", "Revenue", share_column="revenue_share"
            )
        )
        with st.expander("View category figures"):
            money_table(categories, "category")
    with right:
        show_figure(
            charts.horizontal_bar_chart(
                regions, "region", "revenue", "Revenue by Region", "Revenue", share_column="revenue_share"
            )
        )
        with st.expander("View region figures"):
            money_table(regions, "region")


def data_tab(filtered: pd.DataFrame) -> None:
    st.markdown(f"**{format_number(len(filtered))}** order lines match the current filters.")
    st.dataframe(
        to_display_columns(filtered),
        hide_index=True,
        column_config={
            "Order Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Unit Price": st.column_config.NumberColumn(format="dollar"),
            "Revenue": st.column_config.NumberColumn(format="dollar"),
            "Quantity": st.column_config.NumberColumn(format="localized"),
        },
    )
    st.download_button(
        "⬇️ Download filtered data (CSV)",
        data=to_csv_bytes(filtered),
        file_name=f"filtered_sales_{dt.date.today():%Y%m%d}.csv",
        mime="text/csv",
        type="primary",
    )


def show_footer(source_label: str) -> None:
    st.divider()
    st.caption(
        f"**Data source:** {escape_dollars(source_label)}. The bundled dataset is **synthetic demo data** generated by "
        "`scripts/generate_sample_data.py` – it does not describe any real business or customer. "
        "Built with Python, Streamlit, Pandas, NumPy and Matplotlib."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        data, report, source_label, dataset_key = load_data()
    except DataValidationError as error:
        st.title("📊 Business Insights")
        st.error(f"The dashboard could not load any data. {error}")
        st.stop()

    show_cleaning_report(report)
    prepare_filter_state(data, dataset_key)
    filters = sidebar_filters(data)
    filtered = filter_sales_data(
        data,
        start_date=filters["start_date"],
        end_date=filters["end_date"],
        categories=filters["categories"],
        products=filters["products"],
        regions=filters["regions"],
    )

    show_header(source_label, data)

    if filtered.empty:
        st.warning(
            "**No sales match the current filters.** Try widening the date range or removing some selections."
        )
        st.button("↺ Reset filters", on_click=reset_filters, args=(data,), key="reset_filters_empty")
        show_footer(source_label)
        return

    st.caption(
        f"Showing **{format_number(len(filtered))}** of {format_number(len(data))} order lines · "
        f"{describe_filters(filters)}"
    )
    show_kpis(filtered)
    st.write("")
    show_insights(filtered, filters, data)

    st.subheader("📈 Sales Analysis")
    trends, products, segments, raw_data = st.tabs(
        ["Trends", "Products", "Categories & Regions", "Filtered Data & Download"]
    )
    with trends:
        trends_tab(filtered)
    with products:
        products_tab(filtered)
    with segments:
        segments_tab(filtered)
    with raw_data:
        data_tab(filtered)

    show_footer(source_label)


main()
