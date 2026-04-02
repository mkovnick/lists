"""
Compare scraped AutoScout24 car listings side-by-side.

Usage:
    python compare.py                     # Compare all saved cars
    python compare.py --ids ID1 ID2       # Compare specific listings by ID (partial match)
    python compare.py --features          # Focus on feature comparison
    python compare.py --diff              # Show only differences
    python compare.py --value             # Rank by value score (features per EUR)
"""

import json
import sys
from pathlib import Path

DATA_FILE = Path(__file__).parent / "cars.json"

# Spec fields in display order
SPEC_FIELDS = [
    ("Price", "price", lambda v: f"€ {v:,.0f}" if isinstance(v, (int, float)) else str(v)),
    ("Mileage", "mileage_km", lambda v: f"{v:,} km" if isinstance(v, (int, float)) else str(v)),
    ("First Registration", "first_registration", str),
    ("Condition", "condition", str),
    ("Fuel Type", "fuel_type", str),
    ("Power (kW)", "power_kw", str),
    ("Power (HP)", "power_hp", str),
    ("Displacement", "displacement_cc", lambda v: f"{v} cc" if v else "—"),
    ("Cylinders", "cylinders", str),
    ("Transmission", "transmission", str),
    ("Drive Type", "drive_type", str),
    ("Gears", "gears", str),
    ("Body Type", "body_type", str),
    ("Color", "body_color", str),
    ("Paint", "paint_type", str),
    ("Doors", "num_doors", str),
    ("Seats", "num_seats", str),
    ("Previous Owners", "num_previous_owners", str),
    ("CO2 (g/km)", "co2_emissions", str),
    ("Emission Class", "emission_class", str),
    ("Consumption (combined)", "fuel_consumption_combined", str),
    ("Consumption (urban)", "fuel_consumption_urban", str),
    ("Consumption (extra-urban)", "fuel_consumption_extra_urban", str),
    ("Energy Class", "energy_efficiency_class", str),
    ("Non-smoking", "non_smoking_vehicle", str),
    ("Full Service History", "has_full_service_history", str),
    ("Seller", "seller_name", str),
    ("Seller Location", "seller_city", str),
    ("Seller Country", "seller_country", str),
    ("Images", "image_count", str),
]


def load_cars() -> list[dict]:
    if not DATA_FILE.exists():
        print(f"No data file found at {DATA_FILE}. Run scraper.py first.")
        sys.exit(1)
    with open(DATA_FILE) as f:
        return json.load(f)


def car_label(car: dict) -> str:
    parts = [car.get("make", ""), car.get("model", ""), car.get("version", "")]
    label = " ".join(p for p in parts if p).strip()
    return label or car.get("title", car.get("listing_id", "?")[:30])


def print_comparison_table(cars: list[dict], fields: list[tuple], diff_only: bool = False):
    """Print a side-by-side comparison table."""
    labels = [car_label(c) for c in cars]
    col_width = max(30, max((len(l) for l in labels), default=30))
    label_width = max(len(f[0]) for f in fields) + 2

    # Header
    header = f"{'':>{label_width}}"
    for i, label in enumerate(labels):
        short = label[:col_width]
        header += f" | {short:^{col_width}}"
    print(header)
    print("—" * len(header))

    # Rows
    for display_name, key, fmt in fields:
        values = []
        for car in cars:
            raw = car.get(key, "")
            if raw == "" or raw is None:
                values.append("—")
            else:
                try:
                    values.append(fmt(raw))
                except (ValueError, TypeError):
                    values.append(str(raw))

        # Skip row if all values are missing
        if all(v == "—" for v in values):
            continue

        # In diff mode, skip rows where all values are the same
        if diff_only and len(set(values)) == 1:
            continue

        row = f"{display_name:>{label_width}}"
        for val in values:
            row += f" | {val:<{col_width}}"
        print(row)


def print_feature_comparison(cars: list[dict]):
    """Compare features across all cars."""
    labels = [car_label(c) for c in cars]

    # Gather all features
    all_features = set()
    car_features = []
    for car in cars:
        feats = set(car.get("features", []))
        car_features.append(feats)
        all_features.update(feats)

    if not all_features:
        print("\nNo features data available. The scraper may need adjustment for this page structure.")
        return

    print(f"\n{'FEATURE COMPARISON':=^80}")
    print(f"{'':>40}", end="")
    for label in labels:
        print(f" | {label[:15]:^15}", end="")
    print()
    print("—" * (42 + 18 * len(labels)))

    # Sort features, show common first then differences
    common = set.intersection(*car_features) if car_features else set()
    only_some = all_features - common

    if common:
        print(f"\n  Common features ({len(common)}):")
        for feat in sorted(common):
            print(f"    ✓ {feat}")

    if only_some:
        print(f"\n  Differing features ({len(only_some)}):")
        for feat in sorted(only_some):
            row = f"    {feat:>38}"
            for feats in car_features:
                marker = "  ✓" if feat in feats else "  ✗"
                row += f" | {marker:^15}"
            print(row)

    # Summary
    print(f"\n  Feature count:")
    for i, label in enumerate(labels):
        print(f"    {label}: {len(car_features[i])} features")

    unique_counts = []
    for i in range(len(cars)):
        others = set()
        for j in range(len(cars)):
            if j != i:
                others.update(car_features[j])
        unique = car_features[i] - others
        unique_counts.append(unique)

    if any(unique_counts):
        print(f"\n  Unique features (only in one car):")
        for i, label in enumerate(labels):
            if unique_counts[i]:
                print(f"    {label}:")
                for feat in sorted(unique_counts[i]):
                    print(f"      + {feat}")


def print_value_ranking(cars: list[dict]):
    """Rank cars by value: features per EUR and price per km."""
    print(f"\n{'VALUE ANALYSIS':=^80}")

    scored = []
    for car in cars:
        label = car_label(car)
        price = car.get("price")
        mileage = car.get("mileage_km")
        features = car.get("features", [])

        if not isinstance(price, (int, float)) or price <= 0:
            print(f"  {label}: price not available, skipping")
            continue

        score = {
            "label": label,
            "price": price,
            "mileage": mileage if isinstance(mileage, (int, float)) else None,
            "feature_count": len(features),
            "features_per_1000eur": len(features) / (price / 1000) if features else 0,
        }

        if score["mileage"] is not None:
            score["price_per_km"] = price / max(score["mileage"], 1)

        scored.append(score)

    if not scored:
        print("  No cars with valid price data.")
        return

    # Rank by features per EUR
    scored.sort(key=lambda s: s["features_per_1000eur"], reverse=True)
    print(f"\n  Ranked by features per €1,000:")
    for i, s in enumerate(scored, 1):
        print(f"    {i}. {s['label']}")
        print(f"       € {s['price']:,.0f} | {s['feature_count']} features | {s['features_per_1000eur']:.2f} features/€1k")
        if s.get("price_per_km") is not None:
            print(f"       {s['mileage']:,} km | € {s['price_per_km']:.2f}/km")

    # If mileage available, also rank by price-per-km
    with_mileage = [s for s in scored if s.get("price_per_km") is not None]
    if len(with_mileage) > 1:
        with_mileage.sort(key=lambda s: s["price_per_km"])
        print(f"\n  Ranked by price per km (lower = less depreciation cost):")
        for i, s in enumerate(with_mileage, 1):
            print(f"    {i}. {s['label']} — € {s['price_per_km']:.2f}/km ({s['mileage']:,} km)")


def main():
    args = sys.argv[1:]
    cars = load_cars()

    if not cars:
        print("No cars in database. Run scraper.py first.")
        sys.exit(1)

    # Filter by IDs if specified
    if "--ids" in args:
        idx = args.index("--ids")
        ids = args[idx + 1:]
        filtered = []
        for car in cars:
            lid = car.get("listing_id", "")
            if any(partial.lower() in lid.lower() for partial in ids):
                filtered.append(car)
        if not filtered:
            print(f"No cars matched IDs: {ids}")
            print("Available IDs:")
            for car in cars:
                print(f"  {car.get('listing_id', '?')[:60]}")
            sys.exit(1)
        cars = filtered

    diff_only = "--diff" in args
    show_features = "--features" in args
    show_value = "--value" in args

    print(f"\n{'AUTOSCOUT24 CAR COMPARISON':=^80}")
    print(f"  Comparing {len(cars)} car(s)\n")

    # Specs table
    print_comparison_table(cars, SPEC_FIELDS, diff_only=diff_only)

    # Feature comparison
    if show_features or len(args) == 0:
        print_feature_comparison(cars)

    # Value ranking
    if show_value or len(args) == 0:
        print_value_ranking(cars)

    print()


if __name__ == "__main__":
    main()
