"""Loading, validating, cleaning and filtering sales data.

Everything in this module is independent of Streamlit so it can be reused and
unit-tested. The dashboard works with a single *canonical* schema:

    order_id, order_date, product, category, region, quantity, unit_price, revenue

Incoming files may use different header styles ("Order Date", "order-date",
"Date", "Sales", ...). ``normalize_column_names`` maps them onto that schema.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Canonical column name -> label shown to users (and used in CSV downloads).
DISPLAY_NAMES = {
    "order_id": "Order ID",
    "order_date": "Order Date",
    "product": "Product",
    "category": "Category",
    "region": "Region",
    "quantity": "Quantity",
    "unit_price": "Unit Price",
    "revenue": "Revenue",
}
CANONICAL_COLUMNS = list(DISPLAY_NAMES)

# Columns that must exist in an uploaded file.
REQUIRED_COLUMNS = ["order_date", "product", "quantity"]
# At least one of these must exist; the other one is derived.
PRICE_COLUMNS = ["unit_price", "revenue"]
# Text columns that are filled with a placeholder when missing.
OPTIONAL_TEXT_COLUMNS = ["category", "region"]
UNKNOWN_LABEL = "Unknown"

# Common alternative header names (already normalized to snake_case).
COLUMN_ALIASES = {
    "order_id": ["orderid", "order_no", "order_number", "invoice", "invoice_id", "invoice_no", "transaction_id"],
    "order_date": ["date", "orderdate", "purchase_date", "transaction_date", "invoice_date", "sale_date"],
    "product": ["product_name", "item", "item_name", "product_title", "sku_name"],
    "category": ["product_category", "category_name", "department"],
    "region": ["sales_region", "area", "territory", "market"],
    "quantity": ["qty", "units", "quantity_ordered", "units_sold", "order_quantity"],
    "unit_price": ["price", "unitprice", "price_each", "price_per_unit", "item_price"],
    "revenue": ["sales", "total", "amount", "total_sales", "sales_amount", "total_price", "line_total"],
}

SAMPLE_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sales_data.csv"


class DataValidationError(ValueError):
    """Raised when a dataset cannot be used. The message is safe to show to users."""


@dataclass
class CleaningReport:
    """Summary of what ``clean_sales_data`` changed, shown in the dashboard."""

    rows_received: int = 0
    rows_kept: int = 0
    invalid_dates_removed: int = 0
    invalid_numbers_removed: int = 0
    missing_products_removed: int = 0
    duplicates_removed: int = 0
    filled_unknown: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def rows_removed(self) -> int:
        return self.rows_received - self.rows_kept

    def summary_lines(self) -> list[str]:
        """Human-readable list of the changes that were made."""
        lines = []
        if self.invalid_dates_removed:
            lines.append(f"Removed {self.invalid_dates_removed:,} row(s) with a missing or invalid order date.")
        if self.missing_products_removed:
            lines.append(f"Removed {self.missing_products_removed:,} row(s) with no product name.")
        if self.invalid_numbers_removed:
            lines.append(
                f"Removed {self.invalid_numbers_removed:,} row(s) with a missing, invalid or "
                "non-positive quantity/price."
            )
        if self.duplicates_removed:
            lines.append(f"Removed {self.duplicates_removed:,} exact duplicate row(s).")
        for column, count in self.filled_unknown.items():
            lines.append(f'Filled {count:,} missing {DISPLAY_NAMES[column].lower()} value(s) with "{UNKNOWN_LABEL}".')
        lines.extend(self.notes)
        return lines


# ---------------------------------------------------------------------------
# Column names and value parsing
# ---------------------------------------------------------------------------


def to_snake_case(name: object) -> str:
    """Turn a header such as ' Unit Price ($)' into 'unit_price'."""
    text = str(name).strip().lower()
    text = re.sub(r"\(.*?\)", "", text)  # drop units such as "($)"
    text = re.sub(r"[^0-9a-z]+", "_", text)
    return text.strip("_")


def normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with headers mapped to the canonical schema.

    Headers are converted to snake_case and known aliases are renamed. Unknown
    columns are kept (in snake_case) so no user data is silently lost. If two
    headers map to the same canonical name, the first one wins.
    """
    alias_lookup = {canonical: canonical for canonical in CANONICAL_COLUMNS}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_lookup[alias] = canonical

    new_names: list[str] = []
    used: set[str] = set()
    for original in df.columns:
        snake = to_snake_case(original)
        target = alias_lookup.get(snake, snake) or "unnamed"
        if target in used:  # keep duplicates distinguishable instead of overwriting
            suffix = 2
            while f"{target}_{suffix}" in used:
                suffix += 1
            target = f"{target}_{suffix}"
        used.add(target)
        new_names.append(target)

    normalized = df.copy()
    normalized.columns = new_names
    return normalized


def parse_numeric(series: pd.Series) -> pd.Series:
    """Convert values like '$1,299.50', ' 12 ' or '(bad)' to floats (invalid -> NaN)."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype(float)
    cleaned = (
        series.astype("string")
        .str.strip()
        .str.replace(r"[$€£¥₹,\s]", "", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce").astype(float)


def parse_dates(series: pd.Series) -> pd.Series:
    """Convert a column to datetimes; unparseable values become NaT."""
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = series
    else:
        text = series.astype("string").str.strip()
        parsed = pd.to_datetime(text, errors="coerce", format="mixed")
    # Drop timezone information and time-of-day so days group consistently.
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    return parsed.dt.normalize()


def clean_text(series: pd.Series) -> pd.Series:
    """Trim whitespace and collapse repeated spaces; blanks become missing."""
    text = series.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    return text.replace("", pd.NA)


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def read_csv_file(source: str | Path | bytes | io.IOBase) -> pd.DataFrame:
    """Read a CSV from a path, raw bytes or a file-like object.

    Tries a few common encodings and auto-detects the delimiter (comma,
    semicolon, tab). Raises ``DataValidationError`` with a friendly message if
    the file cannot be read.
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")

    if not raw or not raw.strip():
        raise DataValidationError("The file is empty. Please upload a CSV file that contains data.")

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as error:
            last_error = error
            continue
        try:
            return pd.read_csv(io.StringIO(text), sep=None, engine="python", skipinitialspace=True)
        except pd.errors.EmptyDataError as error:
            raise DataValidationError("The file does not contain any columns or rows.") from error
        except (pd.errors.ParserError, csv.Error) as error:
            last_error = error
            break

    raise DataValidationError(
        "The file could not be read as a CSV. Make sure it is a comma-separated text file "
        f"with a header row. (Details: {last_error})"
    )


def validate_columns(df: pd.DataFrame, original_columns: list | None = None) -> None:
    """Check that a normalized DataFrame has the columns the dashboard needs.

    ``original_columns`` (the headers as uploaded) are quoted in the error message.
    """
    if df.empty:
        raise DataValidationError("The dataset has a header row but no data rows.")

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if not any(column in df.columns for column in PRICE_COLUMNS):
        missing.append("unit_price_or_revenue")
    if missing:
        labels = {**DISPLAY_NAMES, "unit_price_or_revenue": "Unit Price or Revenue"}
        readable = ", ".join(labels[column] for column in missing)
        found = ", ".join(str(column) for column in (original_columns or df.columns)) or "none"
        raise DataValidationError(
            f"The dataset is missing required column(s): {readable}. "
            f"Columns found: {found}. See the README for the expected CSV format."
        )


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def clean_sales_data(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
    """Validate and clean a raw sales table.

    Steps:
      1. Normalize column names and check required columns.
      2. Parse dates, numbers and text; drop rows that cannot be used.
      3. Derive whichever of unit price / revenue is missing.
      4. Fill missing category/region with "Unknown" and create order IDs if absent.
      5. Remove exact duplicate rows (before generating any order IDs).

    Returns the cleaned DataFrame (canonical columns only, sorted by date) and a
    ``CleaningReport``. Raises ``DataValidationError`` if nothing usable remains.
    """
    report = CleaningReport(rows_received=len(raw_df))
    df = normalize_column_names(raw_df)
    validate_columns(df, list(raw_df.columns))
    df = df.dropna(how="all").copy()  # drop completely blank lines

    # --- Dates ---------------------------------------------------------------
    df["order_date"] = parse_dates(df["order_date"])
    invalid_dates = df["order_date"].isna()
    report.invalid_dates_removed = int(invalid_dates.sum())
    df = df.loc[~invalid_dates].copy()

    # --- Product names -------------------------------------------------------
    df["product"] = clean_text(df["product"])
    missing_product = df["product"].isna()
    report.missing_products_removed = int(missing_product.sum())
    df = df.loc[~missing_product].copy()

    # --- Numbers -------------------------------------------------------------
    df["quantity"] = parse_numeric(df["quantity"])
    for column in PRICE_COLUMNS:
        if column in df.columns:
            df[column] = parse_numeric(df[column])

    if "revenue" not in df.columns:
        df["revenue"] = df["quantity"] * df["unit_price"]
        report.notes.append("Revenue was calculated as Quantity × Unit Price.")
    elif "unit_price" not in df.columns:
        df["unit_price"] = df["revenue"] / df["quantity"]
        report.notes.append("Unit price was calculated as Revenue ÷ Quantity.")
    else:
        # Fill gaps in one money column from the other where possible.
        missing_revenue = df["revenue"].isna() & df["unit_price"].notna()
        df.loc[missing_revenue, "revenue"] = df.loc[missing_revenue, "quantity"] * df.loc[missing_revenue, "unit_price"]
        missing_price = df["unit_price"].isna() & df["revenue"].notna() & (df["quantity"] > 0)
        df.loc[missing_price, "unit_price"] = df.loc[missing_price, "revenue"] / df.loc[missing_price, "quantity"]

    valid_numbers = (
        df["quantity"].notna()
        & (df["quantity"] > 0)
        & df["unit_price"].notna()
        & (df["unit_price"] >= 0)
        & df["revenue"].notna()
        & (df["revenue"] >= 0)
    )
    report.invalid_numbers_removed = int((~valid_numbers).sum())
    df = df.loc[valid_numbers].copy()

    # --- Optional text columns -----------------------------------------------
    for column in OPTIONAL_TEXT_COLUMNS:
        if column not in df.columns:
            df[column] = UNKNOWN_LABEL
            report.notes.append(f'No {DISPLAY_NAMES[column]} column found; all rows were labelled "{UNKNOWN_LABEL}".')
            continue
        df[column] = clean_text(df[column])
        missing = int(df[column].isna().sum())
        if missing:
            report.filled_unknown[column] = missing
            df[column] = df[column].fillna(UNKNOWN_LABEL)

    # --- Duplicates ---------------------------------------------------------------
    # Removed before any order IDs are generated, otherwise generated IDs would
    # make identical rows look different.
    has_order_ids = "order_id" in df.columns
    df["order_id"] = clean_text(df["order_id"]) if has_order_ids else pd.NA
    df = df[CANONICAL_COLUMNS].copy()
    df["unit_price"] = df["unit_price"].round(2)
    df["revenue"] = df["revenue"].round(2)
    before_dedup = len(df)
    df = df.drop_duplicates().copy()
    report.duplicates_removed = before_dedup - len(df)

    # --- Order IDs ------------------------------------------------------------
    missing_ids = df["order_id"].isna()
    if missing_ids.any():
        df["order_id"] = df["order_id"].astype("string")
        df.loc[missing_ids, "order_id"] = [f"ROW-{number}" for number in range(1, int(missing_ids.sum()) + 1)]
        if has_order_ids:
            report.notes.append(
                f"{int(missing_ids.sum()):,} row(s) had no Order ID and were treated as separate orders."
            )
        else:
            report.notes.append("No Order ID column found; each row was treated as a separate order.")

    df = df.astype(
        {
            "order_id": "string",
            "product": "string",
            "category": "string",
            "region": "string",
            "quantity": float,
            "unit_price": float,
            "revenue": float,
        }
    )
    df = df.sort_values(["order_date", "order_id"], kind="stable").reset_index(drop=True)

    report.rows_kept = len(df)
    if df.empty:
        raise DataValidationError(
            "No usable rows remain after cleaning. Check that dates, quantities and prices are valid."
        )
    return df, report


def load_sales_data(source: str | Path | bytes | io.IOBase) -> tuple[pd.DataFrame, CleaningReport]:
    """Read and clean a CSV in one step."""
    return clean_sales_data(read_csv_file(source))


def load_sample_data() -> tuple[pd.DataFrame, CleaningReport]:
    """Load the bundled synthetic demo dataset."""
    if not SAMPLE_DATA_PATH.exists():
        raise DataValidationError(
            f"Sample dataset not found at {SAMPLE_DATA_PATH}. "
            "Run `python scripts/generate_sample_data.py` to recreate it."
        )
    return load_sales_data(SAMPLE_DATA_PATH)


# ---------------------------------------------------------------------------
# Filtering and export
# ---------------------------------------------------------------------------


def filter_sales_data(
    df: pd.DataFrame,
    start_date=None,
    end_date=None,
    categories: list[str] | None = None,
    products: list[str] | None = None,
    regions: list[str] | None = None,
) -> pd.DataFrame:
    """Return the rows matching every filter.

    An empty or ``None`` selection means "no filter" for that field. Date bounds
    are inclusive.
    """
    mask = pd.Series(True, index=df.index)
    if start_date is not None:
        mask &= df["order_date"] >= pd.Timestamp(start_date)
    if end_date is not None:
        mask &= df["order_date"] <= pd.Timestamp(end_date)
    if categories:
        mask &= df["category"].isin(categories)
    if products:
        mask &= df["product"].isin(products)
    if regions:
        mask &= df["region"].isin(regions)
    return df.loc[mask]


def to_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename canonical columns to their user-facing labels."""
    return df.rename(columns=DISPLAY_NAMES)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialize data for download, with readable headers and ISO dates."""
    export = to_display_columns(df).copy()
    if "Order Date" in export.columns:
        export["Order Date"] = export["Order Date"].dt.strftime("%Y-%m-%d")
    return export.to_csv(index=False).encode("utf-8")
