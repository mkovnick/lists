"""
AutoScout24 car listing scraper using Playwright for JavaScript rendering.

Extracts all specs and optional features/equipment from listing pages.
Features are scraped from the equipment section (expanded via "Show more").

Usage:
    python scraper.py <url>                  # Scrape a single listing
    python scraper.py <url1> <url2> ...      # Scrape multiple listings
    python scraper.py --file urls.txt        # Scrape URLs from a file (one per line)

Scraped data is saved to cars.json in the current directory.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


DATA_FILE = Path(__file__).parent / "cars.json"


def _launch_browser():
    """Launch a Playwright Chromium browser with stealth-ish settings."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        viewport={"width": 1920, "height": 1080},
    )
    return pw, browser, context


def _accept_cookies(page):
    """Dismiss the cookie consent banner if present."""
    try:
        page.locator(
            "button:has-text('Accept'), "
            "button:has-text('Agree'), "
            "#onetrust-accept-btn-handler"
        ).first.click(timeout=5000)
        page.wait_for_timeout(1000)
    except Exception:
        pass


def scrape_listing(url: str) -> dict:
    """Scrape a single AutoScout24 listing page for all specs + features."""
    pw, browser, context = _launch_browser()
    try:
        page = context.new_page()
        # Block heavy resources
        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}",
            lambda route: route.abort(),
        )

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _accept_cookies(page)
        page.wait_for_timeout(3000)

        # Expand "Show more" / "Mehr anzeigen" for equipment section
        try:
            show_more = page.locator(
                "button:has-text('Show more'), "
                "button:has-text('Mehr anzeigen'), "
                "button:has-text('Show all'), "
                "button:has-text('Alle anzeigen')"
            )
            for i in range(show_more.count()):
                try:
                    show_more.nth(i).click(timeout=3000)
                    page.wait_for_timeout(500)
                except Exception:
                    pass
        except Exception:
            pass

        page.wait_for_timeout(1000)
        html = page.content()
    finally:
        browser.close()
        pw.stop()

    return _parse_listing_page(html, url)


def _parse_listing_page(html: str, url: str) -> dict:
    """Parse a listing page using __NEXT_DATA__, JSON-LD, and HTML fallbacks."""
    from bs4 import BeautifulSoup

    car = {}

    # ── 1. Try __NEXT_DATA__ (most complete) ──
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            next_data = json.loads(script.string)
            car = _parse_next_data(next_data)
            car["_source"] = "next_data"
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # ── 2. Try JSON-LD ──
    for script_tag in soup.find_all("script", type="application/ld+json"):
        if not script_tag.string:
            continue
        try:
            ld = json.loads(script_tag.string)
            ld_car = _parse_json_ld(ld)
            for k, v in ld_car.items():
                if v and not car.get(k):
                    car[k] = v
            if not car.get("_source"):
                car["_source"] = "json_ld"
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # ── 3. Always scrape HTML to fill gaps (especially features) ──
    html_car = _parse_html(soup)
    for k, v in html_car.items():
        if k == "features":
            # Merge HTML features with any already found
            existing = set(car.get("features", []))
            existing.update(v)
            car["features"] = sorted(existing)
        elif v and not car.get(k):
            car[k] = v
    if not car.get("_source"):
        car["_source"] = "html"

    car["url"] = url
    car["scraped_at"] = datetime.now().isoformat()
    slug = url.rstrip("/").split("/")[-1]
    # Remove UTM params from slug
    if "?" in slug:
        slug = slug.split("?")[0]
    car.setdefault("listing_id", slug)

    return car


def _parse_next_data(next_data: dict) -> dict:
    """Parse car details from Next.js __NEXT_DATA__ props."""
    car = {}
    props = next_data.get("props", {}).get("pageProps", {})
    listing = props.get("listingDetails", props.get("listing", {}))

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
    car["mileage_km"] = vehicle.get("mileageInKm", vehicle.get("mileage", ""))
    car["first_registration"] = vehicle.get("firstRegistrationDate", "")
    car["fuel_type"] = vehicle.get("fuelType", vehicle.get("fuelCategory", ""))
    car["transmission"] = vehicle.get("transmissionType", "")
    car["drive_type"] = vehicle.get("driveType", "")
    car["power_kw"] = vehicle.get("powerInKw", "")
    car["power_hp"] = vehicle.get("powerInHp", vehicle.get("rawPowerInHp", ""))
    car["displacement_cc"] = vehicle.get("cubicCapacity", vehicle.get("displacementInCcm", ""))
    car["cylinders"] = vehicle.get("numberOfCylinders", "")
    car["gears"] = vehicle.get("numberOfGears", "")
    car["fuel_consumption_combined"] = vehicle.get("fuelConsumptionCombined", "")
    car["co2_emissions"] = vehicle.get("co2Emission", vehicle.get("co2EmissionsCombined", ""))
    car["emission_class"] = vehicle.get("emissionClass", "")
    car["energy_efficiency_class"] = vehicle.get("energyEfficiencyClass", "")
    car["condition"] = vehicle.get("condition", "")
    car["num_previous_owners"] = vehicle.get("numberOfPreviousOwners", "")

    # Price
    pricing = listing.get("prices", listing.get("price", {}))
    if isinstance(pricing, dict):
        car["price"] = pricing.get("publicPrice", pricing.get("price", ""))
        car["currency"] = pricing.get("currency", "EUR")
    elif isinstance(pricing, (int, float)):
        car["price"] = pricing
        car["currency"] = "EUR"

    # Features / equipment
    features = set()

    # Flat list
    for key in ("equipments", "features", "equipment"):
        raw = vehicle.get(key, [])
        if isinstance(raw, list):
            for f in raw:
                name = f if isinstance(f, str) else f.get("label", f.get("name", ""))
                if name:
                    features.add(name)
        elif isinstance(raw, dict):
            for cat, items in raw.items():
                if isinstance(items, list):
                    for f in items:
                        name = f if isinstance(f, str) else f.get("label", f.get("name", ""))
                        if name:
                            features.add(name)

    # Categorized equipment
    for key in ("equipmentsByCategory", "featureCategories"):
        cats = vehicle.get(key, {})
        if isinstance(cats, dict):
            for cat, items in cats.items():
                if isinstance(items, list):
                    for f in items:
                        name = f if isinstance(f, str) else f.get("label", f.get("name", ""))
                        if name:
                            features.add(name)
        elif isinstance(cats, list):
            for cat_obj in cats:
                items = cat_obj.get("equipments", cat_obj.get("items", []))
                for f in items:
                    name = f if isinstance(f, str) else f.get("label", f.get("name", ""))
                    if name:
                        features.add(name)

    car["features"] = sorted(features)

    # Seller
    seller = listing.get("seller", {})
    car["seller_name"] = seller.get("companyName", seller.get("name", ""))
    car["seller_city"] = seller.get("city", seller.get("address", {}).get("city", ""))
    car["seller_country"] = seller.get("countryCode", seller.get("address", {}).get("country", ""))

    car["listing_id"] = listing.get("id", listing.get("classifiedId", ""))

    return car


def _parse_json_ld(item: dict) -> dict:
    """Parse from JSON-LD structured data."""
    car = {}
    item_type = item.get("@type", "")
    if item_type not in ("Car", "Vehicle") and "Auto" not in str(item_type):
        return car
    brand = item.get("brand", "")
    car["make"] = brand.get("name", "") if isinstance(brand, dict) else brand
    car["model"] = item.get("model", "")
    car["fuel_type"] = item.get("fuelType", "")
    car["transmission"] = item.get("vehicleTransmission", "")
    car["body_color"] = item.get("color", "")
    car["first_registration"] = item.get("dateVehicleFirstRegistered", "")
    offers = item.get("offers", {})
    if isinstance(offers, dict):
        car["price"] = offers.get("price", "")
        car["currency"] = offers.get("priceCurrency", "EUR")
    elif isinstance(offers, list) and offers:
        car["price"] = offers[0].get("price", "")
        car["currency"] = offers[0].get("priceCurrency", "EUR")
    return car


def _parse_html(soup) -> dict:
    """Scrape specs and features directly from HTML DOM."""
    car = {}

    # Title
    h1 = soup.find("h1")
    if h1:
        car["title"] = h1.get_text(strip=True)

    # Key-value pairs from dt/dd
    details = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            details[dt.get_text(strip=True)] = dd.get_text(strip=True)
    if details:
        car["details_raw"] = details

    # Features from equipment lists
    features = set()

    # Method 1: <li> items inside equipment/feature containers
    for el in soup.find_all(attrs={"class": re.compile(r"equipment|feature", re.I)}):
        for li in el.find_all("li"):
            text = li.get_text(strip=True)
            if text and len(text) < 200:
                features.add(text)

    # Method 2: data-cy attributes
    for el in soup.find_all(attrs={"data-cy": re.compile(r"equipment|feature", re.I)}):
        text = el.get_text(strip=True)
        if text and len(text) < 200:
            features.add(text)

    # Method 3: sections with category headers (Comfort, Safety, etc.)
    for section_class in ("equipment_comfort", "equipment_entertainment",
                          "equipment_extra", "equipment_safety"):
        section = soup.find(attrs={"class": re.compile(section_class, re.I)})
        if section:
            for li in section.find_all("li"):
                text = li.get_text(strip=True)
                if text and len(text) < 200:
                    features.add(text)

    # Method 4: generic list items that look like features
    for ul in soup.find_all("ul"):
        parent_text = ""
        prev = ul.find_previous_sibling()
        if prev:
            parent_text = prev.get_text(strip=True).lower()
        if any(kw in parent_text for kw in ("equipment", "feature", "ausstattung", "comfort", "safety", "entertainment")):
            for li in ul.find_all("li"):
                text = li.get_text(strip=True)
                if text and len(text) < 200:
                    features.add(text)

    car["features"] = sorted(features)
    return car


def scrape_search_results(url: str, page_num: int = 1) -> tuple[list[dict], int]:
    """Scrape an AutoScout24 search results page. Returns (listings, total_count)."""
    import time

    if page_num > 1:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}page={page_num}"

    pw, browser, context = _launch_browser()
    try:
        page = context.new_page()
        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}",
            lambda route: route.abort(),
        )
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _accept_cookies(page)
        page.wait_for_timeout(3000)
        html = page.content()
    finally:
        browser.close()
        pw.stop()

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    total = 0

    # Try __NEXT_DATA__
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            nd = json.loads(script.string)
            pp = nd.get("props", {}).get("pageProps", {})
            search = pp.get("listings", pp.get("searchResult", {}))
            if isinstance(search, dict):
                total = search.get("totalCount", search.get("numberOfResults", 0))
                items = search.get("listings", search.get("items", search.get("results", [])))
            elif isinstance(search, list):
                items = search
                total = len(items)
            else:
                items = []
            for item in items:
                l = _parse_search_item(item)
                if l:
                    listings.append(l)
            if listings:
                return listings, total
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: parse HTML (ul[role=list] > li)
    ul = soup.find("ul", role="list")
    articles = (ul.find_all("li") if ul else []) or soup.find_all("article")
    for article in articles:
        link = article.find("a", href=re.compile(r"/offers/"))
        if not link:
            continue
        href = link.get("href", "")
        if not href.startswith("http"):
            href = "https://www.autoscout24.com" + href
        listing = {"url": href, "listing_id": href.rstrip("/").split("/")[-1].split("?")[0]}

        title_el = article.find("h2")
        if title_el:
            listing["title"] = title_el.get_text(strip=True)

        text = article.get_text(" ", strip=True)
        km_match = re.search(r"([\d.,]+)\s*km", text)
        if km_match:
            listing["mileage_km"] = int(km_match.group(1).replace(".", "").replace(",", ""))

        price_match = re.search(r"€\s*([\d.,]+)", text)
        if price_match:
            listing["price"] = int(price_match.group(1).replace(".", "").replace(",", ""))
            listing["currency"] = "EUR"

        for color in ("black", "white", "grey", "silver", "blue", "red", "green", "brown", "beige", "orange", "yellow", "gold", "violet"):
            if color in text.lower():
                listing["body_color"] = color
                break

        listings.append(listing)

    count_el = soup.find(string=re.compile(r"[\d,.]+ (results|offers|listings|Ergebnisse)", re.I))
    if count_el:
        m = re.search(r"([\d,.]+)", count_el)
        if m:
            total = int(m.group(1).replace(".", "").replace(",", ""))

    return listings, total


def _parse_search_item(item: dict) -> dict | None:
    """Parse a single item from search results __NEXT_DATA__."""
    l = {}
    vehicle = item.get("vehicle", item)
    l["make"] = vehicle.get("make", item.get("make", ""))
    l["model"] = vehicle.get("model", item.get("model", ""))
    l["version"] = vehicle.get("version", vehicle.get("rawVersion", ""))
    l["mileage_km"] = vehicle.get("mileageInKm", vehicle.get("mileage", ""))
    l["first_registration"] = vehicle.get("firstRegistrationDate", "")
    l["fuel_type"] = vehicle.get("fuelType", vehicle.get("fuelCategory", ""))
    l["power_hp"] = vehicle.get("powerInHp", "")
    l["transmission"] = vehicle.get("transmissionType", "")
    l["body_color"] = vehicle.get("bodyColor", "")

    prices = item.get("prices", item.get("price", {}))
    if isinstance(prices, dict):
        l["price"] = prices.get("publicPrice", prices.get("price", ""))
        l["currency"] = prices.get("currency", "EUR")
    elif isinstance(prices, (int, float)):
        l["price"] = prices

    slug = item.get("url", item.get("detailUrl", item.get("id", "")))
    if slug and not slug.startswith("http"):
        l["url"] = f"https://www.autoscout24.com/offers/{slug}"
    elif slug:
        l["url"] = slug
    l["listing_id"] = item.get("id", item.get("classifiedId", slug or ""))

    return l if l.get("listing_id") or l.get("url") else None


# ─── Search URL builder ───────────────────────────────────────────────────────

COLOR_IDS = {
    "beige": 1, "blue": 2, "brown": 3, "yellow": 5, "grey": 6,
    "green": 7, "orange": 8, "red": 9, "black": 10, "silver": 11,
    "violet": 12, "white": 13, "gold": 14,
}


def build_search_url(*, make="", model="", price_from=None, price_to=None,
                     year_from=None, year_to=None, km_from=None, km_to=None,
                     fuel="", body="", gear="", country="", color="",
                     exclude_colors=None) -> str:
    """Build an AutoScout24 search URL from filter params."""
    base = "https://www.autoscout24.com/lst"
    if make:
        base += f"/{make.lower()}"
    if model:
        base += f"/{model.lower()}"

    params = ["sort=standard", "desc=0", "ustate=N,U"]
    if price_from:
        params.append(f"pricefrom={price_from}")
    if price_to:
        params.append(f"priceto={price_to}")
    if year_from:
        params.append(f"fregfrom={year_from}")
    if year_to:
        params.append(f"fregto={year_to}")
    if km_from:
        params.append(f"kmfrom={km_from}")
    if km_to:
        params.append(f"kmto={km_to}")
    if country:
        params.append(f"cy={country.upper()}")

    # Exclude colors: we can't do this in URL, it's a post-filter
    # Include color:
    if color and not exclude_colors:
        cid = COLOR_IDS.get(color.lower(), color)
        params.append(f"bcol={cid}")

    fuel_map = {"gasoline": "B", "diesel": "D", "electric": "E", "hybrid": "2", "pluginhybrid": "6"}
    if fuel:
        params.append(f"fuel={fuel_map.get(fuel.lower(), fuel)}")

    gear_map = {"automatic": "A", "manual": "M"}
    if gear:
        params.append(f"gear={gear_map.get(gear.lower(), gear)}")

    body_map = {"sedan": 4, "wagon": 6, "coupe": 3, "suv": 12, "convertible": 2, "hatchback": 5}
    if body:
        params.append(f"body={body_map.get(body.lower(), body)}")

    return base + "?" + "&".join(params)


# ─── Database ──────────────────────────────────────────────────────────────────

def load_database() -> list[dict]:
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return []


def save_database(cars: list[dict]):
    with open(DATA_FILE, "w") as f:
        json.dump(cars, f, indent=2, ensure_ascii=False)


def upsert_car(cars: list[dict], new_car: dict) -> list[dict]:
    lid = new_car.get("listing_id", "")
    for i, existing in enumerate(cars):
        if existing.get("listing_id") == lid:
            cars[i] = new_car
            return cars
    cars.append(new_car)
    return cars


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    urls = []
    if args[0] == "--file":
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
            label = f"{car.get('make', '')} {car.get('model', '')}".strip()
            print(f"  Scraped: {label} — €{car.get('price', '?')} — {len(car.get('features', []))} features")
            cars = upsert_car(cars, car)
        except Exception as e:
            print(f"  Error scraping {url}: {e}")

    save_database(cars)
    print(f"Saved {len(cars)} car(s) to {DATA_FILE}")


if __name__ == "__main__":
    main()
