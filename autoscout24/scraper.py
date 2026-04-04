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
    """Launch a Playwright Chromium browser forcing English locale."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        locale="en-GB",
        viewport={"width": 1920, "height": 1080},
        extra_http_headers={
            "Accept-Language": "en-GB,en;q=0.9",
        },
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


def _stringify(val) -> str:
    """Safely convert a value to a string. Handles dicts/lists from JSON."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        # Common patterns: {"formatted": "..."}, {"value": "..."}, {"label": "..."}
        for key in ("formatted", "label", "value", "name", "text", "display"):
            if key in val:
                return str(val[key])
        return ""
    if isinstance(val, (int, float)):
        return val  # keep numeric
    return str(val)


def _extract_feature_name(f) -> str:
    """Extract a feature name from various formats."""
    if isinstance(f, str):
        return f.strip()
    if isinstance(f, dict):
        for key in ("label", "name", "text", "formatted", "value", "description"):
            v = f.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _parse_next_data(next_data: dict) -> dict:
    """Parse car details from Next.js __NEXT_DATA__ props."""
    car = {}
    props = next_data.get("props", {}).get("pageProps", {})
    listing = props.get("listingDetails", props.get("listing", {}))

    vehicle = listing.get("vehicle", {})
    car["make"] = _stringify(vehicle.get("make", ""))
    car["model"] = _stringify(vehicle.get("model", ""))
    car["version"] = _stringify(vehicle.get("rawVersion", vehicle.get("version", "")))
    car["model_year"] = _stringify(vehicle.get("modelYear", ""))
    car["body_type"] = _stringify(vehicle.get("bodyType", ""))
    car["body_color"] = _stringify(vehicle.get("bodyColor", ""))
    car["body_color_original"] = _stringify(vehicle.get("bodyColorOriginal", ""))
    car["paint_type"] = _stringify(vehicle.get("paintType", ""))
    car["num_doors"] = _stringify(vehicle.get("numberOfDoors", ""))
    car["num_seats"] = _stringify(vehicle.get("numberOfSeats", ""))
    car["mileage_km"] = _stringify(vehicle.get("mileageInKm", vehicle.get("mileage", "")))
    car["first_registration"] = _stringify(vehicle.get("firstRegistrationDate", ""))
    car["fuel_type"] = _stringify(vehicle.get("fuelType", vehicle.get("fuelCategory", "")))
    car["transmission"] = _stringify(vehicle.get("transmissionType", ""))
    car["drive_type"] = _stringify(vehicle.get("driveType", ""))
    car["power_kw"] = _stringify(vehicle.get("powerInKw", ""))
    car["power_hp"] = _stringify(vehicle.get("powerInHp", vehicle.get("rawPowerInHp", "")))
    car["displacement_cc"] = _stringify(vehicle.get("cubicCapacity", vehicle.get("displacementInCcm", "")))
    car["cylinders"] = _stringify(vehicle.get("numberOfCylinders", ""))
    car["gears"] = _stringify(vehicle.get("numberOfGears", ""))
    car["fuel_consumption_combined"] = _stringify(vehicle.get("fuelConsumptionCombined", ""))
    car["co2_emissions"] = _stringify(vehicle.get("co2Emission", vehicle.get("co2EmissionsCombined", "")))
    car["emission_class"] = _stringify(vehicle.get("emissionClass", ""))
    car["energy_efficiency_class"] = _stringify(vehicle.get("energyEfficiencyClass", ""))
    car["condition"] = _stringify(vehicle.get("condition", ""))
    car["num_previous_owners"] = _stringify(vehicle.get("numberOfPreviousOwners", ""))

    # Price — try multiple locations
    pricing = listing.get("prices", listing.get("price", listing.get("tracking", {}).get("price", {})))
    if isinstance(pricing, dict):
        for price_key in ("publicPrice", "price", "amount", "value"):
            p = pricing.get(price_key)
            if isinstance(p, (int, float)) and p > 0:
                car["price"] = p
                break
            elif isinstance(p, dict):
                for sub_key in ("value", "amount", "raw"):
                    sv = p.get(sub_key)
                    if isinstance(sv, (int, float)) and sv > 0:
                        car["price"] = sv
                        break
        car["currency"] = _stringify(pricing.get("currency", "EUR"))
    elif isinstance(pricing, (int, float)):
        car["price"] = pricing
        car["currency"] = "EUR"

    # Features / equipment — try every possible location
    features = set()

    def _collect_features(obj):
        """Recursively collect feature names from any structure."""
        if isinstance(obj, str) and obj.strip():
            features.add(obj.strip())
        elif isinstance(obj, list):
            for item in obj:
                name = _extract_feature_name(item)
                if name:
                    features.add(name)
                elif isinstance(item, (dict, list)):
                    _collect_features(item)
        elif isinstance(obj, dict):
            for key, val in obj.items():
                if key in ("equipments", "features", "equipment", "items",
                           "comfort", "safety", "entertainment", "extras",
                           "comfortAndConvenience", "entertainmentAndMedia",
                           "safetyAndSecurity"):
                    _collect_features(val)
                elif isinstance(val, list) and val and isinstance(val[0], (str, dict)):
                    # Looks like a list of features
                    for item in val:
                        name = _extract_feature_name(item)
                        if name:
                            features.add(name)

    # Search in vehicle and listing for equipment data
    for source in (vehicle, listing):
        for key in ("equipments", "features", "equipment", "equipmentsByCategory",
                     "featureCategories", "standardEquipment", "optionalEquipment",
                     "highlightedFeatures", "allEquipments"):
            if key in source:
                _collect_features(source[key])

    car["features"] = sorted(features)

    # Seller
    seller = listing.get("seller", {})
    car["seller_name"] = _stringify(seller.get("companyName", seller.get("name", "")))
    car["seller_city"] = _stringify(seller.get("city", seller.get("address", {}).get("city", "")))
    car["seller_country"] = _stringify(seller.get("countryCode", seller.get("address", {}).get("country", "")))

    car["listing_id"] = _stringify(listing.get("id", listing.get("classifiedId", "")))

    # Debug: save top-level keys so we can diagnose missing data
    car["_vehicle_keys"] = sorted(vehicle.keys()) if vehicle else []
    car["_listing_keys"] = sorted(listing.keys()) if listing else []

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


def _parse_euro_price(text: str) -> int | None:
    """Parse European price format: € 55.950 or € 55,950 or € 55.950,00"""
    m = re.search(r"€?\s*([\d.,\s]+)", text)
    if not m:
        return None
    raw = m.group(1).strip()
    # European: dots are thousands separators, comma is decimal
    if "." in raw and "," in raw:
        # "55.950,00" — dots are thousands, comma is decimal
        raw = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts[-1]) == 3 or len(parts) > 2:
            # "55.950" or "1.234.567" — dots are thousands separators
            raw = raw.replace(".", "")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts[-1]) == 3 or len(parts) > 2:
            # "55,950" — commas are thousands separators
            raw = raw.replace(",", "")
        else:
            # "55,95" — comma is decimal
            raw = raw.replace(",", ".")
    raw = raw.replace(" ", "")
    try:
        val = int(float(raw))
        return val if 500 < val < 10_000_000 else None
    except ValueError:
        return None


def _parse_html(soup) -> dict:
    """Scrape specs and features directly from HTML DOM.

    AutoScout24 renders features in several ways:
    - <li> items inside equipment/feature containers
    - <dd> elements in DataGrid sections
    - Plain text in <span>/<div> elements near category headers
    - Sometimes as comma-separated text blocks
    """
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

    # Price from HTML
    for el in soup.find_all(attrs={"data-testid": re.compile(r"price", re.I)}):
        val = _parse_euro_price(el.get_text(strip=True))
        if val:
            car["price"] = val
            car["currency"] = "EUR"
            break
    if "price" not in car:
        for el in soup.find_all(string=re.compile(r"€\s*[\d.,]+")):
            val = _parse_euro_price(str(el))
            if val:
                car["price"] = val
                car["currency"] = "EUR"
                break

    # ── Feature extraction (multiple strategies) ──
    features = set()

    CATEGORY_KEYWORDS = {
        "comfort", "convenience", "entertainment", "media", "safety", "security",
        "extra", "extras", "equipment", "feature", "features",
        "comfort & convenience", "entertainment & media", "safety & security",
        "ausstattung", "komfort", "sicherheit", "unterhaltung", "multimedia",
        "infotainment", "overige", "exterieur", "interieur", "veiligheid",
        "vehicle description", "general information", "technical information",
    }

    # Regex patterns that indicate a value is a spec/measurement, NOT a feature
    SPEC_PATTERNS = re.compile(
        r"^[\d.,\s]+$"                    # pure numbers: "12", "3,319"
        r"|^\d{1,2}/\d{4}$"              # dates: "10/2025", "02/2026"
        r"|^\d+[\s.,]*\d*\s*(km|kg|kw|hp|ps|cc|mm|cm|l|nm|kwh|kw/h|g/km|s|€)\b"  # measurements
        r"|^\d+\s*(kW|HP|PS|Nm|km/h|km/u)\b"
        r"|^€"                            # prices
        r"|^\d+\.\d+\s*s$"               # acceleration: "6.2 s"
        r"|^\d+\s*cm$"                   # dimensions
        r"|^\d+\s*liter$"               # tank size
        r"|^[\d.,]+\s*%$"               # percentages
        r"|^\w{2,3}\s*\d+$"             # codes: "EU6", "A1"
        , re.IGNORECASE
    )

    # Helper: is text a plausible feature name?
    def _is_feature(text):
        if not text or len(text) > 150 or len(text) < 3:
            return False
        lower = text.lower().strip()
        # Skip section headers
        if lower in CATEGORY_KEYWORDS:
            return False
        # Skip specs and measurements
        if SPEC_PATTERNS.match(text.strip()):
            return False
        # Skip things that are clearly not features
        if text.startswith("€") or text.startswith("$") or text.startswith("http"):
            return False
        # Skip if it's mostly digits (like "001 km" or "005 kg")
        digits = sum(1 for c in text if c.isdigit())
        if len(text) > 0 and digits / len(text) > 0.5:
            return False
        return True

    # Method 1: <li> items inside containers with equipment/feature class names
    for el in soup.find_all(attrs={"class": re.compile(r"equipment|feature|EquipmentBlock", re.I)}):
        for child in el.find_all(["li", "span", "div"]):
            text = child.get_text(strip=True)
            if _is_feature(text) and not child.find(["li", "span", "div"]):
                features.add(text)

    # Method 2: data-cy or data-testid attributes
    for attr in ("data-cy", "data-testid"):
        for el in soup.find_all(attrs={attr: re.compile(r"equipment|feature", re.I)}):
            # Get leaf text nodes
            for child in el.find_all(["li", "span", "div", "p"]):
                text = child.get_text(strip=True)
                if _is_feature(text) and not child.find(["li", "span", "div"]):
                    features.add(text)
            # Also try direct text
            text = el.get_text(strip=True)
            if _is_feature(text):
                features.add(text)

    # Method 3: sections with category headers (Comfort, Safety, etc.)
    for section_class in ("equipment_comfort", "equipment_entertainment",
                          "equipment_extra", "equipment_safety",
                          "EquipmentBlock", "VehicleOverview"):
        for section in soup.find_all(attrs={"class": re.compile(section_class, re.I)}):
            for child in section.find_all(["li", "span", "div"]):
                text = child.get_text(strip=True)
                if _is_feature(text) and not child.find(["li", "span", "div"]):
                    features.add(text)

    # Method 4: dd elements in DataGrid pairs (AutoScout24's detail layout)
    for dd in soup.find_all("dd"):
        cls = " ".join(dd.get("class", []))
        if "DataGrid" in cls or "default" in cls.lower():
            for li in dd.find_all("li"):
                text = li.get_text(strip=True)
                if _is_feature(text):
                    features.add(text)
            # Some dd elements contain comma-separated features
            text = dd.get_text(strip=True)
            if "," in text and len(text) < 500:
                for part in text.split(","):
                    part = part.strip()
                    if _is_feature(part):
                        features.add(part)

    # Method 5: Look for <ul> lists near category headers
    for heading in soup.find_all(["h2", "h3", "h4", "dt", "strong", "b"]):
        heading_text = heading.get_text(strip=True).lower()
        if any(kw in heading_text for kw in CATEGORY_KEYWORDS):
            # Look at the next sibling elements for features
            for sibling in heading.find_next_siblings():
                if sibling.name in ("h2", "h3", "h4"):
                    break  # stop at next section
                for child in sibling.find_all(["li", "span", "div"]):
                    text = child.get_text(strip=True)
                    if _is_feature(text) and not child.find(["li", "span", "div"]):
                        features.add(text)

    # Method 6: Broad fallback — find all <li> items anywhere in the page
    # that look like feature names (short, no children, in a list context)
    if len(features) < 5:
        for li in soup.find_all("li"):
            # Only leaf <li> elements
            if li.find("li"):
                continue
            text = li.get_text(strip=True)
            if _is_feature(text) and len(text) < 80:
                # Avoid nav items, links etc
                parent = li.parent
                if parent and parent.name == "ul":
                    grandparent_class = " ".join(parent.parent.get("class", [])) if parent.parent else ""
                    if "nav" not in grandparent_class.lower() and "menu" not in grandparent_class.lower():
                        features.add(text)

    car["features"] = sorted(features)
    return car


def _set_page_param(url: str, page_num: int) -> str:
    """Set the page= parameter in a URL without re-encoding other params."""
    if re.search(r'[?&]page=\d+', url):
        # Replace existing page= parameter
        return re.sub(r'([?&])page=\d+', rf'\g<1>page={page_num}', url)
    elif '?' in url:
        return url + f'&page={page_num}'
    else:
        return url + f'?page={page_num}'


def scrape_search_results(url: str, page_num: int = 1) -> tuple[list[dict], int]:
    """Scrape an AutoScout24 search results page. Returns (listings, total_count).

    Accepts either a constructed URL or a raw URL pasted from AutoScout24.
    Handles the page= parameter intelligently.
    """
    fetch_url = _set_page_param(url, page_num)
    print(f"[scraper] Fetching page {page_num}: {fetch_url}")

    pw, browser, context = _launch_browser()
    try:
        page = context.new_page()
        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}",
            lambda route: route.abort(),
        )
        page.goto(fetch_url, wait_until="domcontentloaded", timeout=60000)
        _accept_cookies(page)
        page.wait_for_timeout(3000)

        # Scroll down to ensure lazy-loaded content renders
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        html = page.content()
        final_url = page.url
        print(f"[scraper] Final URL after load: {final_url}")
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

            # Log available keys for debugging
            print(f"[scraper] pageProps keys: {list(pp.keys())}")

            # Try multiple possible keys for the search results container
            search = None
            for key in ("listings", "searchResult", "listingSearch", "search", "data"):
                if key in pp:
                    search = pp[key]
                    print(f"[scraper] Found results under key '{key}', type={type(search).__name__}")
                    break

            if search is None:
                # Try one level deeper
                for k, v in pp.items():
                    if isinstance(v, dict) and any(
                        sk in v for sk in ("listings", "items", "results", "totalCount")
                    ):
                        search = v
                        print(f"[scraper] Found results under nested key '{k}'")
                        break

            items = []
            if isinstance(search, dict):
                for tk in ("totalCount", "numberOfResults", "total"):
                    if tk in search:
                        total = search[tk]
                        break
                for ik in ("listings", "items", "results", "edges", "nodes"):
                    if ik in search and isinstance(search[ik], list):
                        items = search[ik]
                        print(f"[scraper] Items key '{ik}', count={len(items)}")
                        break
            elif isinstance(search, list):
                items = search
                total = len(items)

            for item in items:
                # Handle GraphQL-style {node: ...} wrappers
                if isinstance(item, dict) and "node" in item and len(item) <= 2:
                    item = item["node"]
                l = _parse_search_item(item)
                if l:
                    listings.append(l)
            print(f"[scraper] Parsed {len(listings)} listings from __NEXT_DATA__ (total={total})")
            if listings:
                return listings, total
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[scraper] __NEXT_DATA__ parse error: {e}")

    # Fallback: parse HTML (ul[role=list] > li)
    print(f"[scraper] Falling back to HTML parsing for page {page_num}")
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

        price_val = _parse_euro_price(text)
        if price_val:
            listing["price"] = price_val
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
    l["make"] = _stringify(vehicle.get("make", item.get("make", "")))
    l["model"] = _stringify(vehicle.get("model", item.get("model", "")))
    l["version"] = _stringify(vehicle.get("version", vehicle.get("rawVersion", "")))
    l["mileage_km"] = _stringify(vehicle.get("mileageInKm", vehicle.get("mileage", "")))
    l["first_registration"] = _stringify(vehicle.get("firstRegistrationDate", ""))
    l["fuel_type"] = _stringify(vehicle.get("fuelType", vehicle.get("fuelCategory", "")))
    l["power_hp"] = _stringify(vehicle.get("powerInHp", ""))
    l["transmission"] = _stringify(vehicle.get("transmissionType", ""))
    l["body_color"] = _stringify(vehicle.get("bodyColor", ""))

    # Price — handle various formats
    prices = item.get("prices", item.get("price", item.get("tracking", {}).get("price", {})))
    if isinstance(prices, dict):
        for price_key in ("publicPrice", "price", "amount", "value"):
            p = prices.get(price_key)
            if isinstance(p, (int, float)) and p > 0:
                l["price"] = p
                break
            elif isinstance(p, dict):
                for sub_key in ("value", "amount", "raw"):
                    sv = p.get(sub_key)
                    if isinstance(sv, (int, float)) and sv > 0:
                        l["price"] = sv
                        break
        l["currency"] = _stringify(prices.get("currency", "EUR"))
    elif isinstance(prices, (int, float)):
        l["price"] = prices

    slug = item.get("url", item.get("detailUrl", item.get("id", "")))
    if isinstance(slug, dict):
        slug = slug.get("href", slug.get("url", ""))
    slug = _stringify(slug)
    if slug and not slug.startswith("http"):
        l["url"] = f"https://www.autoscout24.com/offers/{slug}"
    elif slug:
        l["url"] = slug
    l["listing_id"] = _stringify(item.get("id", item.get("classifiedId", slug or "")))

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
