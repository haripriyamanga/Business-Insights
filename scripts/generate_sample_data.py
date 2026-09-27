"""Generate the SYNTHETIC demo dataset used by the dashboard.

Every row produced here is fake. No real customers, orders or companies are
represented. The generator uses a fixed random seed so the file is fully
reproducible:

    python scripts/generate_sample_data.py

The output is written to ``data/sales_data.csv``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START_DATE = "2024-01-01"
END_DATE = "2025-12-31"
NUMBER_OF_ORDERS = 1000
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sales_data.csv"

# Product catalogue: (product name, category, base unit price, popularity weight)
PRODUCTS = [
    ("Wireless Earbuds Pro", "Electronics", 89.99, 9),
    ("Smart Fitness Watch", "Electronics", 149.99, 7),
    ("Bluetooth Speaker Mini", "Electronics", 39.99, 8),
    ("USB-C Fast Charger", "Electronics", 24.99, 10),
    ("4K Streaming Stick", "Electronics", 49.99, 6),
    ("Noise-Cancelling Headphones", "Electronics", 229.99, 4),
    ("Stainless Steel Cookware Set", "Home & Kitchen", 129.99, 4),
    ("Electric Kettle", "Home & Kitchen", 34.99, 7),
    ("Ceramic Coffee Mug Set", "Home & Kitchen", 22.99, 8),
    ("Air Fryer 5L", "Home & Kitchen", 99.99, 6),
    ("Bamboo Cutting Board", "Home & Kitchen", 18.99, 7),
    ("Organic Cotton T-Shirt", "Clothing", 19.99, 10),
    ("Slim Fit Denim Jeans", "Clothing", 49.99, 7),
    ("Waterproof Rain Jacket", "Clothing", 79.99, 5),
    ("Merino Wool Socks (3-Pack)", "Clothing", 16.99, 8),
    ("Classic Leather Belt", "Clothing", 29.99, 5),
    ("Yoga Mat Premium", "Sports & Outdoors", 34.99, 7),
    ("Adjustable Dumbbell Pair", "Sports & Outdoors", 179.99, 3),
    ("Insulated Water Bottle", "Sports & Outdoors", 24.99, 9),
    ("Trail Running Shoes", "Sports & Outdoors", 119.99, 5),
    ("Camping Tent 2-Person", "Sports & Outdoors", 139.99, 3),
    ("Vitamin C Face Serum", "Beauty & Personal Care", 27.99, 7),
    ("Electric Toothbrush", "Beauty & Personal Care", 59.99, 6),
    ("Natural Shampoo & Conditioner", "Beauty & Personal Care", 21.99, 8),
    ("Hair Dryer Ionic", "Beauty & Personal Care", 69.99, 4),
    ("Data Science Handbook", "Books", 44.99, 5),
    ("Mindful Productivity Guide", "Books", 16.99, 6),
    ("World Cuisine Cookbook", "Books", 29.99, 5),
    ("Mystery Novel Collection", "Books", 24.99, 6),
]

# Regions and how likely an order is to come from each one.
REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]
REGION_WEIGHTS = [0.24, 0.20, 0.17, 0.14, 0.25]

# Relative order volume by calendar month (holiday peak in Nov/Dec).
MONTH_SEASONALITY = {
    1: 0.75, 2: 0.70, 3: 0.85, 4: 0.90, 5: 0.95, 6: 0.95,
    7: 1.00, 8: 1.00, 9: 0.95, 10: 1.05, 11: 1.45, 12: 1.70,
}


def _order_date_weights(dates: pd.DatetimeIndex) -> np.ndarray:
    """Weight each calendar day by seasonality, a mild upward trend and weekends."""
    seasonality = dates.month.map(MONTH_SEASONALITY).to_numpy(dtype=float)
    trend = np.linspace(1.0, 1.35, len(dates))  # business grows ~35% over the period
    weekend_boost = np.where(dates.dayofweek >= 5, 1.15, 1.0)
    weights = seasonality * trend * weekend_boost
    return weights / weights.sum()


def generate_sales_data(seed: int = SEED) -> pd.DataFrame:
    """Build the synthetic order-line dataset as a DataFrame."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START_DATE, END_DATE, freq="D")
    product_weights = np.array([p[3] for p in PRODUCTS], dtype=float)
    product_weights /= product_weights.sum()

    order_dates = np.sort(
        rng.choice(dates.to_numpy(), size=NUMBER_OF_ORDERS, p=_order_date_weights(dates))
    )

    rows = []
    for order_number, order_date in enumerate(order_dates, start=10001):
        region = rng.choice(REGIONS, p=REGION_WEIGHTS)
        # Most orders contain one product; some contain two or three.
        line_count = rng.choice([1, 2, 3], p=[0.62, 0.28, 0.10])
        product_indexes = rng.choice(len(PRODUCTS), size=line_count, replace=False, p=product_weights)

        for index in product_indexes:
            name, category, base_price, _ = PRODUCTS[index]
            quantity = int(rng.choice([1, 1, 1, 2, 2, 3, 4]))
            # Occasional promotional discounts, deeper during November.
            discount = rng.choice([0.0, 0.10, 0.15], p=[0.75, 0.17, 0.08])
            if pd.Timestamp(order_date).month == 11:
                discount = max(discount, rng.choice([0.0, 0.20], p=[0.5, 0.5]))
            unit_price = round(base_price * (1 - discount), 2)
            rows.append(
                {
                    "Order ID": f"ORD-{order_number}",
                    "Order Date": pd.Timestamp(order_date).strftime("%Y-%m-%d"),
                    "Product": name,
                    "Category": category,
                    "Region": region,
                    "Quantity": quantity,
                    "Unit Price": unit_price,
                    "Revenue": round(quantity * unit_price, 2),
                }
            )

    return pd.DataFrame(rows)


def main() -> None:
    sales = generate_sales_data()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sales.to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(sales):,} synthetic order lines to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
