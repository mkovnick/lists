"""
AutoScout24 car listing scraper using Playwright for JavaScript rendering.

Usage:
    python scraper.py <url>                  # Scrape a single listing
    python scraper.py <url1> <url2> ...      # Scrape multiple listings
    python scraper.py --file urls.txt        # Scrape URLs from a file (one per line)

Scraped data is saved to cars.json in the current directory.
"""

import json
import re
import sys
import hashlib
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup


DATA_FILE = Path(__file__).parent / "cars.json"


def extract_next_data(html: str) -> dict | None:
    """Extract __NEXT_DATA__ JSON embedded by Next.js."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            return json.loads(script.string)
        except json.JSONDecodeError:
            pass
    return None


def extract_json_ld(html: str) -> list[dict]:
    """Extract all JSON-LD structured data blocks."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string:
            try:
                results.append(json.loads(script.string))
            except json.JSONDecodeError:
                continue
    return results


def parse_listing_from_next_data(next_data: dict) -> dict:
    """Parse car details from __NEXT_DATA__ props."""
    car = {}
    try:
        props = next_data.get("props", {}).get("pageProps", {})
        listing = props.get("listingDetails", props.get("listing", {}))

        # Vehicle info
        vehicle = listing.get("vehicle", {})
        car["make"] = vehicle.get("make", "")
        car["model"] = vehicle.get("model", "")
        car["version"] = vehicle.get("rawVersion", vehicle.get("version", ""))
        car["model_year"] = vehicle.get("modelYear", "")
        car["body_type"] = vehicle.get("bodyType", "")
        car["body_color"] = vehicle.get("bodyColor", "")
        car["body_color_original"] = vehicle.get("bodyColorOriginal", "")
        car["paint_type"] = vehicle.get("paintType", "")
        car["num_doors"] = vehicle.get("numberOfDoors", "")
        car["num_seats"] = vehicle.get("numberOfSeats", "")

        # Mileage
        car["mileage_km"] = vehicle.get("mileageInKm", vehicle.get("mileage", ""))

        # Registration
        car["first_registration"] = vehicle.get("firstRegistrationDate", "")

        # Drive
        car["fuel_type"] = vehicle.get("fuelType", vehicle.get("fuelCategory", ""))
        car["transmission"] = vehicle.get("transmissionType", "")
        car["drive_type"] = vehicle.get("driveType", "")
        car["power_kw"] = vehicle.get("powerInKw", "")
        car["power_hp"] = vehicle.get("powerInHp", vehicle.get("rawPowerInHp", ""))
        car["displacement_cc"] = vehicle.get("cubicCapacity", vehicle.get("displacementInCcm", ""))
        car["cylinders"] = vehicle.get("numberOfCylinders", "")
        car["gears"] = vehicle.get("numberOfGears", "")

        # Emissions / Consumption
        car["fuel_consumption_combined"] = vehicle.get("fuelConsumptionCombined", "")
        car["fuel_consumption_urban"] = vehicle.get("fuelConsumptionUrban", "")
        car["fuel_consumption_extra_urban"] = vehicle.get("fuelConsumptionExtraUrban", "")
        car["co2_emissions"] = vehicle.get("co2Emission", vehicle.get("co2EmissionsCombined", ""))
        car["emission_class"] = vehicle.get("emissionClass", "")
        car["emission_label"] = vehicle.get("emissionLabel", "")
        car["energy_efficiency_class"] = vehicle.get("energyEfficiencyClass", "")

        # Condition
        car["condition"] = vehicle.get("condition", "")
        car["num_previous_owners"] = vehicle.get("numberOfPreviousOwners", "")
        car["has_full_service_history"] = vehicle.get("hasFullServiceHistory", "")
        car["non_smoking_vehicle"] = vehicle.get("nonSmokingVehicle", "")

        # Price
        pricing = listing.get("prices", listing.get("price", {}))
        if isinstance(pricing, dict):
            car["price"] = pricing.get("publicPrice", pricing.get("price", ""))
            car["currency"] = pricing.get("currency", "EUR")
            car["price_type"] = pricing.get("priceType", "")
            car["vat_deductible"] = pricing.get("vatDeductible", "")
        elif isinstance(pricing, (int, float)):
            car["price"] = pricing
            car["currency"] = "EUR"

        # Features / equipment
        features_raw = vehicle.get("equipments", vehicle.get("features", []))
        if isinstance(features_raw, list):
            car["features"] = sorted(set(
                f if isinstance(f, str) else f.get("label", f.get("name", str(f)))
                for f in features_raw
            ))
        elif isinstance(features_raw, dict):
            all_features = []
            for category, items in features_raw.items():
                if isinstance(items, list):
                    for item in items:
                        name = item if isinstance(item, str) else item.get("label", item.get("name", str(item)))
                        all_features.append(name)
            car["features"] = sorted(set(all_features))
        else:
            car["features"] = []

        # Feature categories (if available)
        feature_cats = vehicle.get("equipmentsByCategory", vehicle.get("featureCategories", {}))
        if isinstance(feature_cats, dict):
            car["features_by_category"] = {
                cat: sorted(
                    (f if isinstance(f, str) else f.get("label", f.get("name", str(f))))
                    for f in items
                ) if isinstance(items, list) else items
                for cat, items in feature_cats.items()
            }
        elif isinstance(feature_cats, list):
            car["features_by_category"] = {}
            for cat_obj in feature_cats:
                cat_name = cat_obj.get("category", cat_obj.get("name", "Other"))
                items = cat_obj.get("equipments", cat_obj.get("items", []))
                car["features_by_category"][cat_name] = sorted(
                    (f if isinstance(f, str) else f.get("label", f.get("name", str(f))))
                    for f in items
                )

        # Seller info
        seller = listing.get("seller", {})
        car["seller_type"] = seller.get("type", "")
        car["seller_name"] = seller.get("companyName", seller.get("name", ""))
        car["seller_city"] = seller.get("city", seller.get("address", {}).get("city", ""))
        car["seller_country"] = seller.get("countryCode", seller.get("address", {}).get("country", ""))

        # Images
        images = listing.get("images", vehicle.get("images", []))
        if isinstance(images, list):
            car["image_count"] = len(images)

        # Listing metadata
        car["listing_id"] = listing.get("id", listing.get("classifiedId", ""))

    except (KeyError, TypeError, AttributeError) as e:
        car["_parse_warning"] = f"Partial parse: {e}"

    return car


def parse_listing_from_json_ld(json_ld_list: list[dict]) -> dict:
    """Fallback: parse from JSON-LD structured data."""
    car = {}
    for item in json_ld_list:
        item_type = item.get("@type", "")
        if item_type == "Car" or item_type == "Vehicle" or "Auto" in str(item_type):
            car["make"] = item.get("brand", {}).get("name", "") if isinstance(item.get("brand"), dict) else item.get("brand", "")
            car["model"] = item.get("model", "")
            car["body_type"] = item.get("bodyType", "")
            car["fuel_type"] = item.get("fuelType", "")
            car["mileage_km"] = item.get("mileageFromOdometer", {}).get("value", "") if isinstance(item.get("mileageFromOdometer"), dict) else ""
            car["transmission"] = item.get("vehicleTransmission", "")
            car["body_color"] = item.get("color", "")
            car["first_registration"] = item.get("dateVehicleFirstRegistered", item.get("productionDate", ""))
            car["num_doors"] = item.get("numberOfDoors", "")

            offers = item.get("offers", {})
            if isinstance(offers, dict):
                car["price"] = offers.get("price", "")
                car["currency"] = offers.get("priceCurrency", "EUR")
            elif isinstance(offers, list) and offers:
                car["price"] = offers[0].get("price", "")
                car["currency"] = offers[0].get("priceCurrency", "EUR")
    return car


def parse_listing_from_html(html: str) -> dict:
    """Fallback: scrape details from HTML elements directly."""
    soup = BeautifulSoup(html, "html.parser")
    car = {}

    # Title
    title_el = soup.find("h1") or soup.find(attrs={"data-cy": "listing-title"})
    if title_el:
        car["title"] = title_el.get_text(strip=True)

    # Price - look for common price patterns
    price_el = (
        soup.find(attrs={"data-cy": "price"})
        or soup.find(attrs={"class": re.compile(r"price", re.I)})
    )
    if price_el:
        price_text = price_el.get_text(strip=True)
        price_match = re.search(r"[\d.,]+", price_text.replace(".", "").replace(",", "."))
        if price_match:
            car["price_text"] = price_text

    # Key details from dt/dd pairs or key-value sections
    details = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            key = dt.get_text(strip=True)
            val = dd.get_text(strip=True)
            details[key] = val
    car["details_raw"] = details

    # Features - look for lists of equipment/features
    features = set()
    for el in soup.find_all(attrs={"class": re.compile(r"equipment|feature", re.I)}):
        for li in el.find_all("li"):
            text = li.get_text(strip=True)
            if text and len(text) < 200:
                features.add(text)
    if not features:
        for el in soup.find_all(attrs={"data-cy": re.compile(r"equipment|feature", re.I)}):
            text = el.get_text(strip=True)
            if text and len(text) < 200:
                features.add(text)
    car["features"] = sorted(features)

    return car


def scrape_listing(url: str) -> dict:
    """Scrape a single AutoScout24 listing using Playwright."""
    from playwright.sync_api import sync_playwright

    print(f"Scraping: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Block unnecessary resources to speed up loading
        page.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}", lambda route: route.abort())

        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Accept cookies if the banner appears
        try:
            cookie_btn = page.locator("button:has-text('Accept'), button:has-text('Agree'), #onetrust-accept-btn-handler")
            cookie_btn.first.click(timeout=5000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # Wait for content to load
        page.wait_for_timeout(3000)

        html = page.content()
        browser.close()

    # Try parsing methods in order of reliability
    car = {}

    next_data = extract_next_data(html)
    if next_data:
        car = parse_listing_from_next_data(next_data)
        car["_source"] = "next_data"

    json_ld = extract_json_ld(html)
    if json_ld:
        ld_car = parse_listing_from_json_ld(json_ld)
        # Merge JSON-LD data as fallback for missing fields
        for k, v in ld_car.items():
            if v and not car.get(k):
                car[k] = v
        if not car.get("_source"):
            car["_source"] = "json_ld"

    # Always try HTML parsing to fill gaps
    html_car = parse_listing_from_html(html)
    for k, v in html_car.items():
        if v and not car.get(k):
            car[k] = v
    if not car.get("_source"):
        car["_source"] = "html"

    car["url"] = url
    car["scraped_at"] = datetime.now().isoformat()

    # Generate a stable ID from the URL
    slug = url.rstrip("/").split("/")[-1]
    car.setdefault("listing_id", slug)

    return car


def load_database() -> list[dict]:
    """Load saved car data."""
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return []


def save_database(cars: list[dict]):
    """Save car data, updating existing entries by listing_id."""
    with open(DATA_FILE, "w") as f:
        json.dump(cars, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(cars)} car(s) to {DATA_FILE}")


def upsert_car(cars: list[dict], new_car: dict) -> list[dict]:
    """Add or update a car in the list."""
    listing_id = new_car.get("listing_id", "")
    for i, existing in enumerate(cars):
        if existing.get("listing_id") == listing_id:
            cars[i] = new_car
            print(f"  Updated: {new_car.get('make', '')} {new_car.get('model', '')} ({listing_id[:40]}...)")
            return cars
    cars.append(new_car)
    print(f"  Added: {new_car.get('make', '')} {new_car.get('model', '')} ({listing_id[:40]}...)")
    return cars


def print_car_summary(car: dict):
    """Print a readable summary of a scraped car."""
    title = car.get("title", f"{car.get('make', '?')} {car.get('model', '?')} {car.get('version', '')}".strip())
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

    fields = [
        ("Price", lambda c: f"{c.get('currency', 'EUR')} {c['price']:,.0f}" if isinstance(c.get('price'), (int, float)) else c.get('price_text', c.get('price', '—'))),
        ("Mileage", lambda c: f"{c['mileage_km']:,} km" if isinstance(c.get('mileage_km'), (int, float)) else c.get('mileage_km', '—')),
        ("First reg.", lambda c: c.get('first_registration', '—')),
        ("Fuel", lambda c: c.get('fuel_type', '—')),
        ("Power", lambda c: f"{c['power_kw']} kW / {c['power_hp']} HP" if c.get('power_kw') else c.get('power_hp', '—')),
        ("Transmission", lambda c: c.get('transmission', '—')),
        ("Drive", lambda c: c.get('drive_type', '—')),
        ("Displacement", lambda c: f"{c['displacement_cc']} cc" if c.get('displacement_cc') else '—'),
        ("Body", lambda c: f"{c.get('body_type', '—')} / {c.get('body_color', '—')}"),
        ("Condition", lambda c: c.get('condition', '—')),
        ("Seller", lambda c: f"{c.get('seller_name', '—')} ({c.get('seller_city', '—')}, {c.get('seller_country', '—')})"),
        ("CO2", lambda c: f"{c['co2_emissions']} g/km" if c.get('co2_emissions') else '—'),
        ("Emission class", lambda c: c.get('emission_class', '—')),
    ]

    for label, fn in fields:
        try:
            val = fn(car)
        except (KeyError, TypeError):
            val = "—"
        if val and val != "—" and val != " / ":
            print(f"  {label:20s}: {val}")

    features = car.get("features", [])
    if features:
        print(f"\n  Features ({len(features)}):")
        for f in features:
            print(f"    • {f}")

    features_by_cat = car.get("features_by_category", {})
    if features_by_cat:
        print(f"\n  Features by category:")
        for cat, items in sorted(features_by_cat.items()):
            print(f"    [{cat}]")
            for item in items:
                print(f"      • {item}")
    print()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    urls = []
    if args[0] == "--file":
        if len(args) < 2:
            print("Error: --file requires a filename argument")
            sys.exit(1)
        with open(args[1]) as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        urls = [a for a in args if a.startswith("http")]

    if not urls:
        print("No valid URLs provided.")
        sys.exit(1)

    cars = load_database()

    for url in urls:
        try:
            car = scrape_listing(url)
            print_car_summary(car)
            cars = upsert_car(cars, car)
        except Exception as e:
            print(f"Error scraping {url}: {e}")

    save_database(cars)


if __name__ == "__main__":
    main()
