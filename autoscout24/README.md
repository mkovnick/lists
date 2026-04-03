# AutoScout24 Car Feature Comparator

Scrapes AutoScout24 listings and shows a side-by-side feature matrix so you can see exactly which optional extras each car is missing.

## Setup (one-time)

```bash
git clone https://github.com/mkovnick/lists.git
cd lists
git checkout claude/autoscout24-scraper-3Cmo2
bash autoscout24/setup.sh
```

This creates a Python virtual environment in `autoscout24/.venv` and installs Playwright + Chromium.

## Run the app

```bash
autoscout24/.venv/bin/python autoscout24/app.py --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` on your Mac, or `http://<your-mac-ip>:8080` on your iPhone (same Wi-Fi). The app prints your local IP on startup.

## How to use

1. **Go to AutoScout24** in your browser and set up your search filters (make, model, color, equipment, country, etc.)
2. **Copy the search results URL** from your browser's address bar
3. **Paste it** into the Search tab in the app and hit "Scrape All Listings"
4. Wait while it scrapes each listing page (~20s per car, with 2s delays between)
5. When done, it auto-switches to the **Features** tab showing the comparison matrix

The feature matrix shows:
- **Missing features count** per car at the top
- **Differences** section: features that not all cars have (sorted rarest first)
- **Common** section: features every car has

You can also add individual listings via the **Add URL** tab (paste a single listing URL).

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask web app (UI + API) |
| `scraper.py` | Playwright scraper + parsers |
| `cars.json` | Saved car data (auto-generated) |
| `setup.sh` | One-time setup script |
| `requirements.txt` | Python dependencies |

## Tips

- If port 8080 is taken, use `--port 9000` or any other port
- To reset your saved cars, delete `cars.json` or remove cars in the UI
- Features are scraped in English (browser sends `Accept-Language: en`)
- The scraper clicks "Show more" buttons to expand hidden equipment sections
- AutoScout24 may rate-limit if you scrape too fast; the 2s delay between listings helps avoid this
