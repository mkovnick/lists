"""
Search AutoScout24 listings with filters and scrape matching results.

Usage:
    python search.py --make mercedes-benz --model c-class
    python search.py --make mercedes-benz --model c-class --exclude-color black --require-feature "360"
    python search.py --make mercedes-benz --model c-class --country DE --price-to 50000
    python search.py --help

Filters:
    --make          Make (e.g. mercedes-benz, bmw, audi)
    --model         Model (e.g. c-class, 3-series, a4)
    --price-from    Minimum price in EUR
    --price-to      Maximum price in EUR
    --year-from     Minimum first registration year
    --year-to       Maximum first registration year
    --km-to         Maximum mileage in km
    --km-from       Minimum mileage in km
    --fuel          Fuel type: gasoline, diesel, electric, hybrid, pluginhybrid
    --body          Body type: sedan, wagon, coupe, suv, convertible, hatchback
    --country       Country code: DE, NL, AT, BE, FR, IT, ES, etc.
    --gear          Transmission: automatic, manual
    --color         Body color: black, white, grey, silver, blue, red, etc.
    --exclude-color Exclude this color (can be repeated)
    --require-feature  Only keep listings containing this text in features (can be repeated)
    --pages         Number of search result pages to scan (default: 1)
    --scrape        Scrape full details for each matching listing (default: metadata only)
    --max           Maximum number of listings to scrape in detail (default: 10)
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

DATA_FILE = Path(__file__).parent / "cars.json"

# AutoScout24 search URL color mapping
COLOR_IDS = {
    "beige": 1, "blue": 2, "brown": 3, "yellow": 5, "grey": 6,
    "green": 7, "orange": 8, "red": 9, "black": 10, "silver": 11,
    "violet": 12, "white": 13, "gold": 14,
}

FUEL_IDS = {
    "gasoline": "B", "diesel": "D", "electric": "E",
    "hybrid": "2", "pluginhybrid": "6", "lpg": "L", "cng": "C",
    "hydrogen": "H", "ethanol": "X",
}

BODY_IDS = {
    "sedan": 4, "wagon": 6, "coupe": 3, "suv": 12,
    "convertible": 2, "hatchback": 5, "van": 7, "transporter": 10,
}

GEAR_IDS = {
    "automatic": "A", "manual": "M", "semiautomatic": "S",
}


def build_search_url(args) -> str:
    """Build an AutoScout24 search URL from filters."""
    base = "https://www.autoscout24.com/lst"

    if args.make:
        base += f"/{args.make.lower()}"
    if args.model:
        base += f"/{args.model.lower()}"

    params = []
    params.append("sort=standard")
    params.append("desc=0")
    params.append("ustate=N,U")  # new and used

    if args.price_from:
        params.append(f"pricefrom={args.price_from}")
    if args.price_to:
        params.append(f"priceto={args.price_to}")
    if args.year_from:
        params.append(f"fregfrom={args.year_from}")
    if args.year_to:
        params.append(f"fregto={args.year_to}")
    if args.km_from:
        params.append(f"kmfrom={args.km_from}")
    if args.km_to:
        params.append(f"kmto={args.km_to}")
    if args.fuel:
        fid = FUEL_IDS.get(args.fuel.lower(), args.fuel)
        params.append(f"fuel={fid}")
    if args.body:
        bid = BODY_IDS.get(args.body.lower(), args.body)
        params.append(f"body={bid}")
    if args.gear:
        gid = GEAR_IDS.get(args.gear.lower(), args.gear)
        params.append(f"gear={gid}")
    if args.country:
        params.append(f"cy={args.country.upper()}")
    if args.color:
        cid = COLOR_IDS.get(args.color.lower(), args.color)
        params.append(f"bcol={cid}")

    url = base + "?" + "&".join(params)
    return url


def scrape_search_results(url: str, page_num: int = 1) -> tuple[list[dict], int]:
    """Scrape a search results page. Returns (listings_metadata, total_results)."""
    from playwright.sync_api import sync_playwright

    if page_num > 1:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}page={page_num}"

    print(f"  Fetching search page {page_num}: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}", lambda route: route.abort())

        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Accept cookies
        try:
            cookie_btn = page.locator("button:has-text('Accept'), button:has-text('Agree'), #onetrust-accept-btn-handler")
            cookie_btn.first.click(timeout=5000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()

    return parse_search_results(html)


def parse_search_results(html: str) -> tuple[list[dict], int]:
    """Parse search results page HTML into listing metadata."""
    listings = []
    total = 0
    soup = BeautifulSoup(html, "html.parser")

    # Try __NEXT_DATA__ first (most reliable)
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            next_data = json.loads(script.string)
            page_props = next_data.get("props", {}).get("pageProps", {})

            # Search results are usually in listings or searchResult
            search_data = page_props.get("listings", page_props.get("searchResult", {}))

            if isinstance(search_data, dict):
                total = search_data.get("totalCount", search_data.get("numberOfResults", 0))
                items = search_data.get("listings", search_data.get("items", search_data.get("results", [])))
            elif isinstance(search_data, list):
                items = search_data
                total = len(items)
            else:
                items = []

            for item in items:
                listing = parse_search_item(item)
                if listing:
                    listings.append(listing)

            if listings:
                return listings, total
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: parse HTML directly
    # AutoScout24 uses article tags or divs with data-cy attributes for listings
    articles = soup.find_all("article") or soup.find_all(attrs={"data-cy": re.compile(r"listing|result", re.I)})

    for article in articles:
        listing = {}

        # Title / link
        link = article.find("a", href=re.compile(r"/offers/"))
        if link:
            href = link.get("href", "")
            if not href.startswith("http"):
                href = "https://www.autoscout24.com" + href
            listing["url"] = href
            listing["listing_id"] = href.rstrip("/").split("/")[-1]

        title_el = article.find("h2") or article.find(attrs={"data-cy": re.compile(r"title", re.I)})
        if title_el:
            listing["title"] = title_el.get_text(strip=True)

        # Price
        price_el = article.find(attrs={"data-cy": re.compile(r"price", re.I)}) or article.find(string=re.compile(r"€"))
        if price_el:
            price_text = price_el if isinstance(price_el, str) else price_el.get_text(strip=True)
            nums = re.findall(r"[\d]+", price_text.replace(".", "").replace(",", ""))
            if nums:
                listing["price"] = int(nums[0])
                listing["currency"] = "EUR"

        # Details text (mileage, year, fuel, etc.)
        detail_spans = article.find_all("span")
        details_text = " ".join(s.get_text(strip=True) for s in detail_spans)

        km_match = re.search(r"([\d.,]+)\s*km", details_text)
        if km_match:
            listing["mileage_km"] = int(km_match.group(1).replace(".", "").replace(",", ""))

        year_match = re.search(r"(\d{2}/\d{4})", details_text)
        if year_match:
            listing["first_registration"] = year_match.group(1)

        hp_match = re.search(r"(\d+)\s*(?:HP|PS|hp)", details_text)
        if hp_match:
            listing["power_hp"] = int(hp_match.group(1))

        for fuel in ["Gasoline", "Diesel", "Electric", "Hybrid", "Plug-in"]:
            if fuel.lower() in details_text.lower():
                listing["fuel_type"] = fuel
                break

        for trans in ["Automatic", "Manual"]:
            if trans.lower() in details_text.lower():
                listing["transmission"] = trans
                break

        # Color from details (if visible)
        for color_name in COLOR_IDS:
            if color_name.lower() in details_text.lower():
                listing["body_color"] = color_name
                break

        if listing.get("url"):
            listings.append(listing)

    # Total count from header
    count_el = soup.find(string=re.compile(r"[\d,.]+ (results|offers|listings)", re.I))
    if count_el:
        count_match = re.search(r"([\d,.]+)", count_el)
        if count_match:
            total = int(count_match.group(1).replace(".", "").replace(",", ""))

    return listings, total


def parse_search_item(item: dict) -> dict | None:
    """Parse a single search result item from __NEXT_DATA__."""
    listing = {}
    try:
        # Handle different data structures
        vehicle = item.get("vehicle", item)
        listing["make"] = vehicle.get("make", item.get("make", ""))
        listing["model"] = vehicle.get("model", item.get("model", ""))
        listing["version"] = vehicle.get("version", vehicle.get("rawVersion", ""))
        listing["mileage_km"] = vehicle.get("mileageInKm", vehicle.get("mileage", ""))
        listing["first_registration"] = vehicle.get("firstRegistrationDate", "")
        listing["fuel_type"] = vehicle.get("fuelType", vehicle.get("fuelCategory", ""))
        listing["power_hp"] = vehicle.get("powerInHp", vehicle.get("rawPowerInHp", ""))
        listing["power_kw"] = vehicle.get("powerInKw", "")
        listing["transmission"] = vehicle.get("transmissionType", "")
        listing["body_type"] = vehicle.get("bodyType", "")
        listing["body_color"] = vehicle.get("bodyColor", "")

        # Price
        prices = item.get("prices", item.get("price", {}))
        if isinstance(prices, dict):
            listing["price"] = prices.get("publicPrice", prices.get("price", ""))
            listing["currency"] = prices.get("currency", "EUR")
        elif isinstance(prices, (int, float)):
            listing["price"] = prices

        # URL
        slug = item.get("url", item.get("detailUrl", item.get("id", "")))
        if slug and not slug.startswith("http"):
            listing["url"] = f"https://www.autoscout24.com/offers/{slug}"
        elif slug:
            listing["url"] = slug

        listing["listing_id"] = item.get("id", item.get("classifiedId", slug or ""))

        # Features from search results (usually just a summary)
        equipment = vehicle.get("equipments", vehicle.get("highlights", []))
        if isinstance(equipment, list):
            listing["search_features"] = [
                f if isinstance(f, str) else f.get("label", f.get("name", str(f)))
                for f in equipment
            ]

    except (KeyError, TypeError):
        return None

    return listing if listing.get("listing_id") or listing.get("url") else None


def apply_post_filters(listings: list[dict], exclude_colors: list[str], require_features: list[str]) -> list[dict]:
    """Filter listings by color exclusion and required features."""
    filtered = []
    for listing in listings:
        # Color exclusion
        color = str(listing.get("body_color", "")).lower()
        if any(exc.lower() in color for exc in exclude_colors):
            continue

        # Feature requirement (checked against search_features if available)
        if require_features:
            search_feats = " ".join(listing.get("search_features", [])).lower()
            title = listing.get("title", "").lower()
            version = listing.get("version", "").lower()
            combined = f"{search_feats} {title} {version}"
            # Features might only be visible on the detail page, so we mark for later check
            listing["_feature_check_needed"] = []
            for req in require_features:
                if req.lower() not in combined:
                    listing["_feature_check_needed"].append(req)

        filtered.append(listing)
    return filtered


def check_features_on_detail(listing: dict, required: list[str]) -> bool:
    """Scrape the detail page and check if required features exist."""
    from scraper import scrape_listing

    url = listing.get("url", "")
    if not url:
        return False

    try:
        full = scrape_listing(url)
        all_features_text = " ".join(full.get("features", [])).lower()
        cats = full.get("features_by_category", {})
        for items in cats.values():
            if isinstance(items, list):
                all_features_text += " " + " ".join(str(i).lower() for i in items)

        for req in required:
            if req.lower() not in all_features_text:
                return False

        # Merge full data into the listing
        listing.update(full)
        return True
    except Exception as e:
        print(f"    Warning: could not scrape {url}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Search AutoScout24 listings")
    parser.add_argument("--make", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--price-from", type=int)
    parser.add_argument("--price-to", type=int)
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--km-from", type=int)
    parser.add_argument("--km-to", type=int)
    parser.add_argument("--fuel", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--gear", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--color", default="")
    parser.add_argument("--exclude-color", action="append", default=[])
    parser.add_argument("--require-feature", action="append", default=[])
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--scrape", action="store_true", help="Scrape full details for matches")
    parser.add_argument("--max", type=int, default=10, help="Max listings to scrape in detail")
    args = parser.parse_args()

    if not args.make and not args.model:
        parser.print_help()
        sys.exit(1)

    search_url = build_search_url(args)
    print(f"\nSearch URL: {search_url}\n")

    all_listings = []
    total = 0

    for page_num in range(1, args.pages + 1):
        listings, page_total = scrape_search_results(search_url, page_num)
        if page_total:
            total = page_total
        all_listings.extend(listings)
        print(f"  Found {len(listings)} listings on page {page_num}")

        if page_num < args.pages:
            time.sleep(2)  # polite delay between pages

    print(f"\nTotal results on site: {total}")
    print(f"Fetched metadata for: {len(all_listings)} listings")

    # Apply post-filters
    if args.exclude_color or args.require_feature:
        filtered = apply_post_filters(all_listings, args.exclude_color, args.require_feature)
        removed = len(all_listings) - len(filtered)
        if args.exclude_color:
            print(f"After excluding color(s) {args.exclude_color}: {len(filtered)} remaining ({removed} removed)")
        all_listings = filtered

    if not all_listings:
        print("No listings match your criteria.")
        return

    # Print summary of search results
    print(f"\n{'SEARCH RESULTS':=^80}")
    for i, listing in enumerate(all_listings, 1):
        label = listing.get("title", f"{listing.get('make', '')} {listing.get('model', '')} {listing.get('version', '')}".strip())
        price = listing.get("price", "?")
        price_str = f"€ {price:,.0f}" if isinstance(price, (int, float)) else str(price)
        mileage = listing.get("mileage_km", "?")
        mile_str = f"{mileage:,} km" if isinstance(mileage, (int, float)) else str(mileage)
        color = listing.get("body_color", "?")
        feats = listing.get("search_features", [])
        check = listing.get("_feature_check_needed", [])

        print(f"\n  {i}. {label}")
        print(f"     {price_str} | {mile_str} | {color} | {listing.get('fuel_type', '?')} | {listing.get('transmission', '?')}")
        if feats:
            print(f"     Features: {', '.join(feats[:8])}{'...' if len(feats) > 8 else ''}")
        if check:
            print(f"     ⚠ Need detail scrape to verify: {', '.join(check)}")

    # Scrape full details if requested
    if args.scrape:
        print(f"\n{'SCRAPING FULL DETAILS':=^80}")
        cars = []
        if DATA_FILE.exists():
            with open(DATA_FILE) as f:
                cars = json.load(f)

        scraped = 0
        for listing in all_listings:
            if scraped >= args.max:
                print(f"\n  Reached max scrape limit ({args.max}). Use --max to increase.")
                break

            needed = listing.get("_feature_check_needed", [])
            if needed:
                print(f"\n  Checking features for: {listing.get('title', listing.get('listing_id', '?')[:40])}")
                has_features = check_features_on_detail(listing, needed)
                if not has_features:
                    print(f"    ✗ Missing required feature(s), skipping")
                    continue
                print(f"    ✓ Has all required features")
            else:
                # Scrape full details
                from scraper import scrape_listing
                url = listing.get("url", "")
                if url:
                    try:
                        full = scrape_listing(url)
                        listing.update(full)
                    except Exception as e:
                        print(f"    Error: {e}")
                        continue

            # Clean up internal fields
            listing.pop("_feature_check_needed", None)
            listing.pop("search_features", None)

            # Upsert into database
            lid = listing.get("listing_id", "")
            updated = False
            for j, existing in enumerate(cars):
                if existing.get("listing_id") == lid:
                    cars[j] = listing
                    updated = True
                    break
            if not updated:
                cars.append(listing)

            scraped += 1
            time.sleep(2)  # polite delay

        with open(DATA_FILE, "w") as f:
            json.dump(cars, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(cars)} total car(s) to {DATA_FILE}")
        print(f"Scraped {scraped} new listing(s) in detail")
    else:
        print(f"\nTip: add --scrape to fetch full details and features for these listings")
        print(f"     add --scrape --max 20 to scrape up to 20 listings")


if __name__ == "__main__":
    main()
