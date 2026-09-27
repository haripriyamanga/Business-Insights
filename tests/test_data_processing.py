"""Tests for loading, cleaning and filtering sales data."""

import pandas as pd
import pytest

from utils.data_processing import (
    CANONICAL_COLUMNS,
    DataValidationError,
    clean_sales_data,
    filter_sales_data,
    load_sales_data,
    load_sample_data,
    normalize_column_names,
    parse_numeric,
    read_csv_file,
    to_csv_bytes,
)


def make_raw(**overrides):
    """A small, valid raw dataset using the standard headers."""
    data = {
        "Order ID": ["A1", "A1", "A2", "A3"],
        "Order Date": ["2024-01-05", "2024-01-05", "2024-02-10", "2024-03-15"],
        "Product": ["Mug", "Kettle", "Mug", "Lamp"],
        "Category": ["Kitchen", "Kitchen", "Kitchen", "Home"],
        "Region": ["North", "North", "South", "West"],
        "Quantity": [2, 1, 3, 1],
        "Unit Price": [10.0, 30.0, 10.0, 45.0],
        "Revenue": [20.0, 30.0, 30.0, 45.0],
    }
    data.update(overrides)
    return pd.DataFrame(data)


# --- Column names ------------------------------------------------------------


def test_normalize_column_names_handles_styles_and_aliases():
    raw = pd.DataFrame(columns=[" Order ID ", "order-date", "Item", "QTY", "Price ($)", "Sales", "Notes"])
    assert list(normalize_column_names(raw).columns) == [
        "order_id", "order_date", "product", "quantity", "unit_price", "revenue", "notes",
    ]


def test_normalize_column_names_keeps_duplicate_headers_distinct():
    raw = pd.DataFrame(columns=["Revenue", "Sales"])
    assert list(normalize_column_names(raw).columns) == ["revenue", "revenue_2"]


# --- Value parsing -------------------------------------------------------------


def test_parse_numeric_strips_currency_and_separators():
    parsed = parse_numeric(pd.Series(["$1,299.50", " 12 ", "abc", None, "€5"]))
    assert parsed.iloc[0] == 1299.5
    assert parsed.iloc[1] == 12
    assert pd.isna(parsed.iloc[2]) and pd.isna(parsed.iloc[3])
    assert parsed.iloc[4] == 5


# --- Cleaning --------------------------------------------------------------------


def test_clean_valid_data_keeps_all_rows_and_canonical_schema():
    cleaned, report = clean_sales_data(make_raw())
    assert list(cleaned.columns) == CANONICAL_COLUMNS
    assert len(cleaned) == 4
    assert report.rows_removed == 0
    assert pd.api.types.is_datetime64_any_dtype(cleaned["order_date"])
    assert cleaned["revenue"].sum() == pytest.approx(125.0)


def test_clean_removes_invalid_dates_and_numbers():
    raw = make_raw(
        **{
            "Order Date": ["2024-01-05", "not a date", "2024-02-10", ""],
            "Quantity": [2, 1, "three", 1],
        }
    )
    cleaned, report = clean_sales_data(raw)
    assert report.invalid_dates_removed == 2
    assert report.invalid_numbers_removed == 1
    assert list(cleaned["order_id"]) == ["A1"]


def test_clean_removes_non_positive_quantities():
    cleaned, report = clean_sales_data(make_raw(Quantity=[2, 0, -1, 1]))
    assert report.invalid_numbers_removed == 2
    assert len(cleaned) == 2


def test_clean_removes_exact_duplicates():
    raw = pd.concat([make_raw(), make_raw().iloc[[0]]], ignore_index=True)
    cleaned, report = clean_sales_data(raw)
    assert report.duplicates_removed == 1
    assert len(cleaned) == 4


def test_duplicates_removed_even_without_order_id_column():
    raw = pd.concat([make_raw(), make_raw().iloc[[2]]], ignore_index=True).drop(columns=["Order ID"])
    cleaned, report = clean_sales_data(raw)
    assert report.duplicates_removed == 1
    assert cleaned["order_id"].is_unique and len(cleaned) == 4


def test_clean_fills_missing_category_and_region():
    cleaned, report = clean_sales_data(make_raw(Category=["Kitchen", None, " ", "Home"]))
    assert (cleaned["category"] == "Unknown").sum() == 2
    assert report.filled_unknown == {"category": 2}


def test_clean_calculates_revenue_when_missing():
    raw = make_raw().drop(columns=["Revenue"])
    cleaned, report = clean_sales_data(raw)
    assert cleaned["revenue"].tolist() == [20.0, 30.0, 30.0, 45.0]
    assert any("Revenue was calculated" in note for note in report.notes)


def test_clean_calculates_unit_price_when_missing():
    cleaned, _ = clean_sales_data(make_raw().drop(columns=["Unit Price"]))
    assert cleaned["unit_price"].tolist() == [10.0, 30.0, 10.0, 45.0]


def test_clean_fills_individual_missing_revenue_from_price():
    cleaned, _ = clean_sales_data(make_raw(Revenue=[20.0, None, 30.0, 45.0]))
    assert cleaned.loc[cleaned["product"] == "Kettle", "revenue"].item() == 30.0


def test_optional_columns_can_be_absent():
    raw = make_raw().drop(columns=["Order ID", "Category", "Region"])
    cleaned, report = clean_sales_data(raw)
    assert cleaned["order_id"].nunique() == 4  # every row becomes its own order
    assert set(cleaned["category"]) == {"Unknown"}
    assert set(cleaned["region"]) == {"Unknown"}
    assert len(report.notes) == 3


def test_missing_required_columns_raise_friendly_error():
    with pytest.raises(DataValidationError, match="Order Date"):
        clean_sales_data(make_raw().drop(columns=["Order Date"]))
    with pytest.raises(DataValidationError, match="Unit Price or Revenue"):
        clean_sales_data(make_raw().drop(columns=["Unit Price", "Revenue"]))


def test_empty_dataset_raises():
    with pytest.raises(DataValidationError):
        clean_sales_data(make_raw().iloc[0:0])


def test_dataset_with_no_usable_rows_raises():
    with pytest.raises(DataValidationError, match="No usable rows"):
        clean_sales_data(make_raw(**{"Order Date": ["x", "y", "z", "?"]}))


# --- Reading files --------------------------------------------------------------


def test_read_csv_detects_semicolon_delimiter():
    content = b"Order Date;Product;Quantity;Revenue\n2024-01-01;Mug;2;20\n"
    cleaned, _ = load_sales_data(content)
    assert cleaned["revenue"].item() == 20.0


def test_read_csv_handles_bom_and_latin1():
    utf8_bom = "﻿Order Date,Product,Quantity,Revenue\n2024-01-01,Café Mug,1,9.5\n".encode("utf-8")
    latin1 = "Order Date,Product,Quantity,Revenue\n2024-01-01,Café Mug,1,9.5\n".encode("latin-1")
    for content in (utf8_bom, latin1):
        cleaned, _ = load_sales_data(content)
        assert cleaned["product"].item() == "Café Mug"


def test_read_empty_file_raises():
    with pytest.raises(DataValidationError, match="empty"):
        read_csv_file(b"   ")


# --- Filtering and export --------------------------------------------------------


def test_filter_sales_data_combines_filters():
    cleaned, _ = clean_sales_data(make_raw())
    assert len(filter_sales_data(cleaned)) == 4
    assert len(filter_sales_data(cleaned, start_date="2024-02-01")) == 2
    assert len(filter_sales_data(cleaned, end_date="2024-02-10")) == 3  # end date is inclusive
    assert len(filter_sales_data(cleaned, categories=["Kitchen"], regions=["North"])) == 2
    assert filter_sales_data(cleaned, products=["Lamp"])["product"].tolist() == ["Lamp"]
    assert filter_sales_data(cleaned, categories=["Garden"]).empty


def test_to_csv_bytes_round_trips():
    cleaned, _ = clean_sales_data(make_raw())
    exported = to_csv_bytes(cleaned).decode("utf-8")
    assert exported.splitlines()[0] == "Order ID,Order Date,Product,Category,Region,Quantity,Unit Price,Revenue"
    reloaded, _ = load_sales_data(exported.encode("utf-8"))
    pd.testing.assert_frame_equal(reloaded, cleaned)


def test_sample_dataset_loads_cleanly():
    sample, report = load_sample_data()
    assert len(sample) >= 300
    assert report.rows_removed == 0
    assert sample["order_date"].notna().all()
    assert (sample["revenue"] > 0).all()
