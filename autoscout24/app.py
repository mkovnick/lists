"""
AutoScout24 Car Scraper & Feature Comparison — Mobile Web App

Search for cars, scrape all their features, and see a comparison chart
showing which optional features each car has or is missing.

Run on your Mac:
    pip install flask playwright && python -m playwright install chromium
    python app.py

Then open http://localhost:5000 in Safari (Mac or iPhone on same Wi-Fi).
For iPhone access: python app.py --host 0.0.0.0
"""

import json
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response

DATA_FILE = Path(__file__).parent / "cars.json"
app = Flask(__name__)

# Background job state
_job = {"running": False, "status": "", "progress": 0, "total": 0, "log": []}


def _log(msg):
    _job["log"].append(msg)
    _job["status"] = msg
    print(f"  [{_job['progress']}/{_job['total']}] {msg}")


def load_cars():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return []


def save_cars(cars):
    with open(DATA_FILE, "w") as f:
        json.dump(cars, f, indent=2, ensure_ascii=False)


def upsert(cars, car):
    lid = car.get("listing_id", "")
    for i, c in enumerate(cars):
        if c.get("listing_id") == lid:
            cars[i] = car
            return cars
    cars.append(car)
    return cars


# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return Response(INDEX_HTML, content_type="text/html; charset=utf-8")


@app.route("/api/cars")
def api_cars():
    return jsonify(load_cars())


@app.route("/api/delete", methods=["POST"])
def api_delete():
    lid = request.json.get("listing_id", "")
    cars = [c for c in load_cars() if c.get("listing_id") != lid]
    save_cars(cars)
    return jsonify({"ok": True, "count": len(cars)})


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Scrape a single listing URL."""
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        from scraper import scrape_listing
        car = scrape_listing(url)
        cars = load_cars()
        cars = upsert(cars, car)
        save_cars(cars)
        return jsonify({"ok": True, "car": car})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search", methods=["POST"])
def api_search():
    """Start a background search + scrape job."""
    if _job["running"]:
        return jsonify({"error": "A search is already running"}), 409

    params = request.json
    t = threading.Thread(target=_search_worker, args=(params,), daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "Search started"})


@app.route("/api/search/status")
def api_search_status():
    return jsonify({
        "running": _job["running"],
        "status": _job["status"],
        "progress": _job["progress"],
        "total": _job["total"],
        "log": _job["log"][-20:],
    })


def _search_worker(params):
    """Background worker: search → filter → scrape each listing."""
    _job.update(running=True, status="Starting search...", progress=0, total=0, log=[])

    try:
        from scraper import build_search_url, scrape_search_results, scrape_listing

        search_url = build_search_url(
            make=params.get("make", ""),
            model=params.get("model", ""),
            price_from=params.get("price_from"),
            price_to=params.get("price_to"),
            year_from=params.get("year_from"),
            year_to=params.get("year_to"),
            km_to=params.get("km_to"),
            fuel=params.get("fuel", ""),
            gear=params.get("gear", ""),
            country=params.get("country", ""),
        )
        _log(f"Search URL: {search_url}")

        pages = min(int(params.get("pages", 2)), 10)
        max_scrape = min(int(params.get("max_scrape", 15)), 50)
        exclude_colors = [c.strip().lower() for c in params.get("exclude_colors", "").split(",") if c.strip()]
        require_features = [f.strip().lower() for f in params.get("require_features", "").split(",") if f.strip()]

        # Step 1: Gather search result metadata
        all_listings = []
        for pg in range(1, pages + 1):
            _log(f"Fetching search page {pg}/{pages}...")
            listings, total = scrape_search_results(search_url, pg)
            all_listings.extend(listings)
            _log(f"  Got {len(listings)} listings (total on site: {total})")
            if pg < pages:
                time.sleep(2)

        # Step 2: Filter by color
        if exclude_colors:
            before = len(all_listings)
            all_listings = [
                l for l in all_listings
                if not any(ec in str(l.get("body_color", "")).lower() for ec in exclude_colors)
            ]
            _log(f"Color filter: {before} → {len(all_listings)} (excluded {', '.join(exclude_colors)})")

        if not all_listings:
            _log("No listings found matching filters.")
            return

        # Step 3: Scrape each listing in detail for features
        _job["total"] = min(len(all_listings), max_scrape)
        cars = load_cars()
        scraped = 0

        for i, listing in enumerate(all_listings):
            if scraped >= max_scrape:
                break

            url = listing.get("url", "")
            if not url:
                continue

            label = listing.get("title", f"{listing.get('make', '')} {listing.get('model', '')}").strip()
            _job["progress"] = scraped + 1
            _log(f"Scraping [{scraped+1}/{_job['total']}]: {label[:50]}...")

            try:
                car = scrape_listing(url)

                # Check required features
                if require_features:
                    car_feats = " ".join(car.get("features", [])).lower()
                    missing = [rf for rf in require_features if rf not in car_feats]
                    if missing:
                        _log(f"  Skipped — missing: {', '.join(missing)}")
                        continue

                cars = upsert(cars, car)
                scraped += 1
                feat_count = len(car.get("features", []))
                _log(f"  OK — €{car.get('price', '?')} — {feat_count} features")

            except Exception as e:
                _log(f"  Error: {e}")

            time.sleep(2)  # polite delay

        save_cars(cars)
        _log(f"Done! Scraped {scraped} cars. Total in database: {len(cars)}")

    except Exception as e:
        _log(f"Search failed: {e}")
    finally:
        _job["running"] = False


# ── HTML Frontend ─────────────────────────────────────────────────────────────

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>AutoScout24 Car Compare</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--accent:#f78166;--text:#e6edf3;
--muted:#8b949e;--green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--text);
-webkit-text-size-adjust:100%;overflow-x:hidden}
.container{max-width:900px;margin:0 auto;padding:12px}
h1{font-size:1.3rem;margin:8px 0 2px}
.sub{color:var(--muted);font-size:.8rem;margin-bottom:12px}

/* Tabs */
.tabs{display:flex;border-bottom:2px solid var(--border);margin-bottom:14px;position:sticky;top:0;
background:var(--bg);z-index:10;padding-top:4px}
.tab{flex:1;text-align:center;padding:10px 4px;font-size:.85rem;font-weight:600;
color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px}
.tab.active{color:var(--accent);border-color:var(--accent)}
.pane{display:none}.pane.active{display:block}

/* Inputs */
label{display:block;color:var(--muted);font-size:.75rem;margin-bottom:3px;margin-top:8px}
input,select{width:100%;padding:9px 10px;border-radius:6px;border:1px solid var(--border);
background:var(--card);color:var(--text);font-size:.95rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.btn{display:block;width:100%;padding:12px;border:none;border-radius:8px;font-size:.95rem;
font-weight:600;cursor:pointer;margin-top:12px;text-align:center}
.btn-primary{background:var(--accent);color:#fff}
.btn-secondary{background:var(--border);color:var(--text)}
.btn:disabled{opacity:.4}

/* Cards */
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px;margin:8px 0}
.card h3{font-size:.9rem;margin-bottom:6px}
.card .meta{font-size:.8rem;color:var(--muted)}
.price{color:var(--green);font-weight:700;font-size:1.1rem}
.feat-count{display:inline-block;background:var(--border);border-radius:4px;padding:1px 6px;font-size:.75rem}
.remove-btn{background:none;border:1px solid var(--border);color:var(--red);border-radius:6px;
padding:4px 10px;font-size:.75rem;cursor:pointer;margin-top:6px}

/* Progress */
.progress-bar{height:4px;background:var(--border);border-radius:2px;margin:8px 0;overflow:hidden}
.progress-fill{height:100%;background:var(--accent);transition:width .3s}
.log{background:var(--card);border-radius:8px;padding:10px;font-size:.75rem;font-family:monospace;
max-height:200px;overflow-y:auto;color:var(--muted);margin-top:8px;white-space:pre-wrap}

/* Feature matrix */
.matrix-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:12px}
.matrix{border-collapse:collapse;font-size:.75rem;min-width:100%}
.matrix th,.matrix td{padding:5px 8px;border:1px solid var(--border);white-space:nowrap}
.matrix th{position:sticky;left:0;background:var(--card);z-index:2;text-align:left;max-width:180px;
overflow:hidden;text-overflow:ellipsis;font-weight:500}
.matrix thead th{position:sticky;top:0;background:var(--bg);z-index:3;text-align:center;font-weight:600}
.matrix thead th:first-child{z-index:4}
.matrix .has{background:#1a3a1a;color:var(--green);text-align:center}
.matrix .miss{background:#3a1a1a;color:var(--red);text-align:center}
.matrix .category-row td,.matrix .category-row th{background:var(--border);font-weight:700;
color:var(--text);font-size:.8rem;padding:8px}
.count-row td{font-weight:700;font-size:.8rem}

/* Stats */
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.stat{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 12px;flex:1;min-width:100px}
.stat .val{font-size:1.2rem;font-weight:700;color:var(--accent)}
.stat .lbl{font-size:.7rem;color:var(--muted)}

.empty{text-align:center;color:var(--muted);padding:30px 0}
.tag{display:inline-block;background:var(--border);border-radius:4px;padding:2px 7px;
font-size:.72rem;margin:2px}
.filter-row{display:flex;gap:6px;margin:8px 0;align-items:center}
.filter-row input{flex:1}
.filter-row .btn{margin:0;width:auto;padding:9px 14px;flex-shrink:0}
</style>
</head>
<body>
<div class="container">
<h1>AutoScout24 Compare</h1>
<p class="sub">Search, scrape & compare car features</p>

<div class="tabs">
<div class="tab active" data-t="search">Search</div>
<div class="tab" data-t="add">Add URL</div>
<div class="tab" data-t="cars">Cars <span id="cnt"></span></div>
<div class="tab" data-t="matrix">Features</div>
</div>

<!-- ══ SEARCH ══ -->
<div id="search" class="pane active">
<div class="row">
 <div><label>Make</label><input id="s-make" value="mercedes-benz"></div>
 <div><label>Model</label><input id="s-model" value="c-class"></div>
</div>
<div class="row">
 <div><label>Price from (€)</label><input type="number" id="s-pfrom"></div>
 <div><label>Price to (€)</label><input type="number" id="s-pto"></div>
</div>
<div class="row">
 <div><label>Year from</label><input type="number" id="s-yfrom"></div>
 <div><label>Max km</label><input type="number" id="s-kmto"></div>
</div>
<div class="row">
 <div><label>Exclude colors</label><input id="s-excol" value="black" placeholder="black, brown"></div>
 <div><label>Must have feature</label><input id="s-feat" value="360" placeholder="e.g. 360"></div>
</div>
<div class="row">
 <div><label>Pages to scan</label><input type="number" id="s-pages" value="2" min="1" max="10"></div>
 <div><label>Max cars to scrape</label><input type="number" id="s-max" value="15" min="1" max="50"></div>
</div>
<button class="btn btn-primary" id="search-btn" onclick="startSearch()">Search & Scrape All Features</button>

<div id="search-progress" style="display:none">
 <div class="progress-bar"><div class="progress-fill" id="pbar"></div></div>
 <div id="ptext" style="font-size:.8rem;color:var(--muted)"></div>
 <div class="log" id="plog"></div>
</div>
</div>

<!-- ══ ADD URL ══ -->
<div id="add" class="pane">
<label>Paste AutoScout24 listing URL</label>
<input type="url" id="add-url" placeholder="https://www.autoscout24.com/offers/...">
<button class="btn btn-primary" onclick="scrapeOne()">Scrape This Listing</button>
<div id="add-status"></div>
</div>

<!-- ══ MY CARS ══ -->
<div id="cars" class="pane">
<div id="car-list"></div>
</div>

<!-- ══ FEATURE MATRIX ══ -->
<div id="matrix" class="pane">
<div class="filter-row">
 <input id="feat-filter" placeholder="Filter features..." oninput="renderMatrix()">
 <button class="btn btn-secondary" onclick="document.getElementById('feat-filter').value='';renderMatrix()">Clear</button>
</div>
<div class="stats" id="matrix-stats"></div>
<div class="matrix-wrap" id="matrix-out"></div>
</div>

</div>
<script>
let cars=[];
let pollTimer=null;

// Tabs
document.querySelectorAll('.tab').forEach(t=>{
 t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById(t.dataset.t).classList.add('active');
  if(t.dataset.t==='cars')renderCars();
  if(t.dataset.t==='matrix')renderMatrix();
 };
});

// Load cars
async function loadCars(){
 cars=await(await fetch('/api/cars')).json();
 document.getElementById('cnt').textContent='('+cars.length+')';
}

// Search
async function startSearch(){
 const p={
  make:v('s-make'),model:v('s-model'),
  price_from:n('s-pfrom'),price_to:n('s-pto'),
  year_from:n('s-yfrom'),km_to:n('s-kmto'),
  exclude_colors:v('s-excol'),require_features:v('s-feat'),
  pages:n('s-pages')||2,max_scrape:n('s-max')||15
 };
 document.getElementById('search-btn').disabled=true;
 document.getElementById('search-progress').style.display='block';
 try{
  const r=await(await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(r.error){alert(r.error);return;}
  pollTimer=setInterval(pollSearch,1500);
 }catch(e){alert(e);}
}

async function pollSearch(){
 const r=await(await fetch('/api/search/status')).json();
 const pct=r.total?Math.round(r.progress/r.total*100):0;
 document.getElementById('pbar').style.width=pct+'%';
 document.getElementById('ptext').textContent=r.status;
 document.getElementById('plog').textContent=r.log.join('\n');
 document.getElementById('plog').scrollTop=9999;
 if(!r.running){
  clearInterval(pollTimer);
  document.getElementById('search-btn').disabled=false;
  await loadCars();
  // Auto-switch to feature matrix
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(x=>x.classList.remove('active'));
  document.querySelector('[data-t="matrix"]').classList.add('active');
  document.getElementById('matrix').classList.add('active');
  renderMatrix();
 }
}

// Scrape single
async function scrapeOne(){
 const url=v('add-url');
 if(!url)return;
 document.getElementById('add-status').innerHTML='<div class="card">Scraping... ~20s</div>';
 try{
  const r=await(await fetch('/api/scrape',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})})).json();
  if(r.error){document.getElementById('add-status').innerHTML='<div class="card" style="color:var(--red)">'+esc(r.error)+'</div>';return;}
  const c=r.car;
  document.getElementById('add-status').innerHTML=`<div class="card"><h3>${esc(c.make)} ${esc(c.model)}</h3><span class="price">${fmtP(c.price)}</span> · ${(c.features||[]).length} features scraped</div>`;
  await loadCars();
 }catch(e){document.getElementById('add-status').innerHTML='<div class="card" style="color:var(--red)">'+esc(''+e)+'</div>';}
}

// Car list
function renderCars(){
 const el=document.getElementById('car-list');
 if(!cars.length){el.innerHTML='<p class="empty">No cars yet. Use Search or Add URL.</p>';return;}
 el.innerHTML=cars.map(c=>{
  const lbl=[c.make,c.model,c.version].filter(Boolean).join(' ')||c.title||'?';
  const fc=(c.features||[]).length;
  return`<div class="card"><h3>${esc(lbl)}</h3>
   <span class="price">${fmtP(c.price)}</span>
   <span class="meta"> · ${fmtKm(c.mileage_km)} · ${esc(c.body_color||'?')} · ${esc(c.fuel_type||'?')}</span>
   <span class="feat-count">${fc} features</span>
   ${fc?`<details style="margin-top:6px"><summary style="font-size:.75rem;color:var(--muted);cursor:pointer">Show features</summary>
   <div style="margin-top:4px">${c.features.map(f=>'<span class="tag">'+esc(f)+'</span>').join('')}</div></details>`:''}
   <button class="remove-btn" onclick="delCar('${esc(c.listing_id)}')">Remove</button>
  </div>`;
 }).join('');
}

async function delCar(lid){
 if(!confirm('Remove this car?'))return;
 await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({listing_id:lid})});
 await loadCars();renderCars();
}

// ═══ FEATURE COMPARISON MATRIX ═══
function renderMatrix(){
 const out=document.getElementById('matrix-out');
 const statsEl=document.getElementById('matrix-stats');
 const filter=(document.getElementById('feat-filter').value||'').toLowerCase();

 if(cars.length<1){out.innerHTML='<p class="empty">Add at least 1 car to see features.</p>';statsEl.innerHTML='';return;}

 // Collect all features per car
 const carFeats=cars.map(c=>new Set((c.features||[]).map(f=>f)));
 const allFeats=new Set();
 carFeats.forEach(s=>s.forEach(f=>allFeats.add(f)));
 let feats=[...allFeats].sort();

 if(!feats.length){out.innerHTML='<p class="empty">No feature data. Scrape listings to get features.</p>';statsEl.innerHTML='';return;}

 // Apply text filter
 if(filter)feats=feats.filter(f=>f.toLowerCase().includes(filter));

 // Classify: common (all have), partial (some have)
 const common=feats.filter(f=>carFeats.every(s=>s.has(f)));
 const partial=feats.filter(f=>!carFeats.every(s=>s.has(f)));

 // Car labels
 const labels=cars.map(c=>{
  let l=[c.make,c.model].filter(Boolean).join(' ')||c.title||'?';
  if(l.length>18)l=l.slice(0,16)+'…';
  return l;
 });

 // Stats
 const totalAllFeats=[...allFeats].length;
 statsEl.innerHTML=`
  <div class="stat"><div class="val">${cars.length}</div><div class="lbl">Cars</div></div>
  <div class="stat"><div class="val">${totalAllFeats}</div><div class="lbl">Total features</div></div>
  <div class="stat"><div class="val">${common.length}</div><div class="lbl">All cars have</div></div>
  <div class="stat"><div class="val" style="color:var(--red)">${partial.length}</div><div class="lbl">Differences</div></div>
 `;

 // Build table
 let h='<table class="matrix"><thead><tr><th>Feature</th>';
 labels.forEach((l,i)=>{
  const p=fmtP(cars[i].price);
  h+=`<th>${esc(l)}<br><span style="font-weight:400;font-size:.7rem">${p}</span></th>`;
 });
 h+='</tr></thead><tbody>';

 // Missing features count per car
 const missingCounts=cars.map((_,i)=>partial.filter(f=>!carFeats[i].has(f)).length);

 // Summary row
 h+='<tr class="count-row"><th style="color:var(--red)">Missing features</th>';
 missingCounts.forEach(n=>h+=`<td style="text-align:center;color:var(--red);font-size:.9rem">${n}</td>`);
 h+='</tr>';

 h+='<tr class="count-row"><th style="color:var(--green)">Total features</th>';
 carFeats.forEach(s=>h+=`<td style="text-align:center;color:var(--green)">${s.size}</td>`);
 h+='</tr>';

 // Differing features first (most interesting)
 if(partial.length){
  h+=`<tr class="category-row"><th colspan="${cars.length+1}">⚡ Differences — features NOT all cars have (${partial.length})</th></tr>`;
  // Sort: features that fewer cars have first (rarest first)
  partial.sort((a,b)=>{
   const ca=carFeats.filter(s=>s.has(a)).length;
   const cb=carFeats.filter(s=>s.has(b)).length;
   return ca-cb||a.localeCompare(b);
  });
  for(const f of partial){
   h+=`<tr><th>${esc(f)}</th>`;
   carFeats.forEach(s=>{
    h+=s.has(f)?'<td class="has">✓</td>':'<td class="miss">✗</td>';
   });
   h+='</tr>';
  }
 }

 // Common features
 if(common.length){
  h+=`<tr class="category-row"><th colspan="${cars.length+1}">✓ Common — all cars have (${common.length})</th></tr>`;
  for(const f of common){
   h+=`<tr><th>${esc(f)}</th>`;
   carFeats.forEach(()=>h+='<td class="has">✓</td>');
   h+='</tr>';
  }
 }

 h+='</tbody></table>';
 out.innerHTML=h;
}

// Helpers
function v(id){return document.getElementById(id).value.trim();}
function n(id){const x=v(id);return x?Number(x):null;}
function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function fmtP(v){return typeof v==='number'?'€'+v.toLocaleString():(v||'—');}
function fmtKm(v){return typeof v==='number'?v.toLocaleString()+' km':(v||'—');}

loadCars();
</script>
</body></html>"""

if __name__ == "__main__":
    host = "0.0.0.0" if "--host" in sys.argv else "127.0.0.1"
    port = 5000
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    print(f"\n  🚗 AutoScout24 Compare running at http://{host}:{port}")
    if host == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            print(f"  📱 From iPhone: http://{ip}:{port}")
        except Exception:
            pass
    print(f"  Press Ctrl+C to stop\n")
    app.run(host=host, port=port, debug=False)
