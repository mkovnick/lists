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
    """Background worker: search + batch URLs → scrape each listing."""
    _job.update(running=True, status="Starting...", progress=0, total=0, log=[])

    try:
        from scraper import scrape_search_results, scrape_listing

        direct_urls = params.get("urls", [])
        search_url = params.get("search_url", "").strip()

        all_listings = []

        # Step 1a: Fetch from search URL if provided
        if search_url and "autoscout24" in search_url:
            _log(f"Search URL: {search_url}")
            max_pages = min(int(params.get("pages", 5)), 20)

            total_on_site = 0
            for pg in range(1, max_pages + 1):
                _log(f"Fetching search page {pg}...")
                listings, total = scrape_search_results(search_url, pg)
                if total:
                    total_on_site = total
                all_listings.extend(listings)
                _log(f"  Got {len(listings)} listings (total on site: {total_on_site})")
                if not listings:
                    _log(f"  Empty page — no more results.")
                    break
                # Keep going if we haven't collected all results yet
                if len(all_listings) >= total_on_site and total_on_site > 0:
                    _log(f"  Collected all {len(all_listings)} of {total_on_site} listings.")
                    break
                if pg < max_pages:
                    time.sleep(2)

            if total_on_site > len(all_listings):
                _log(f"  Warning: site reports {total_on_site} results but only scraped {len(all_listings)}")

        # Step 1b: Add individual URLs
        if direct_urls:
            _log(f"Adding {len(direct_urls)} individual URL(s)")
            for url in direct_urls:
                url = url.strip()
                # Strip tracking params but keep the offer path
                if "?" in url:
                    url = url.split("?")[0]
                slug = url.rstrip("/").split("/")[-1]
                all_listings.append({"url": url, "listing_id": slug})

        if not search_url and not direct_urls:
            _log("Error: no URLs or search URL provided")
            return

        # Deduplicate by listing_id
        seen = set()
        deduped = []
        for l in all_listings:
            lid = l.get("listing_id", "")
            if lid and lid not in seen:
                seen.add(lid)
                deduped.append(l)
        if len(deduped) < len(all_listings):
            _log(f"Merged & deduplicated: {len(all_listings)} → {len(deduped)} unique listings")
        all_listings = deduped

        if not all_listings:
            _log("No listings found matching filters.")
            return

        # Step 2: Scrape each listing in detail for features
        _job["total"] = len(all_listings)
        cars = load_cars()
        scraped = 0

        for i, listing in enumerate(all_listings):

            url = listing.get("url", "")
            if not url:
                continue

            label = listing.get("title", f"{listing.get('make', '')} {listing.get('model', '')}").strip()
            _job["progress"] = scraped + 1
            _log(f"Scraping [{scraped+1}/{_job['total']}]: {label[:50]}...")

            try:
                car = scrape_listing(url)
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
.matrix thead th{position:sticky;top:0;background:var(--bg);z-index:3;text-align:center;font-weight:600;white-space:normal;min-width:120px;max-width:200px}
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
<div class="tab active" data-t="scrape">Scrape</div>
<div class="tab" data-t="cars">Cars <span id="cnt"></span></div>
<div class="tab" data-t="matrix">Export</div>
</div>

<!-- ══ SCRAPE ══ -->
<div id="scrape" class="pane active">
<label>Search URL (optional — scrapes all results from your search)</label>
<input type="url" id="s-url" placeholder="https://www.autoscout24.com/lst/mercedes-benz/...">

<label style="margin-top:12px">Individual listing URLs (optional — one per line)</label>
<textarea id="add-urls" rows="6" style="width:100%;padding:10px;border-radius:6px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:.8rem;font-family:monospace" placeholder="https://www.autoscout24.com/offers/...
https://www.autoscout24.com/offers/..."></textarea>

<p style="font-size:.72rem;color:var(--muted);margin-top:4px">Use both fields together — results are merged and deduplicated. The search URL finds all listings matching your filters, and individual URLs catch any extras you spotted.</p>

<div class="row" style="margin-top:8px">
 <div><label>Search pages</label><input type="number" id="s-pages" value="3" min="1" max="10"></div>
 <div></div>
</div>
<button class="btn btn-primary" id="scrape-btn" onclick="startScrape()">Scrape Everything</button>

<div id="scrape-progress" style="display:none">
 <div class="progress-bar"><div class="progress-fill" id="pbar"></div></div>
 <div id="ptext" style="font-size:.8rem;color:var(--muted)"></div>
 <div class="log" id="plog"></div>
</div>
</div>

<!-- ══ MY CARS ══ -->
<div id="cars" class="pane">
<div id="car-list"></div>
</div>

<!-- ══ EXPORT FOR CLAUDE ══ -->
<div id="matrix" class="pane">
<div style="display:flex;gap:8px;align-items:end;margin-bottom:10px">
 <div style="flex:1"><label>Filter by model</label>
  <select id="model-filter" onchange="renderMatrix()" style="width:100%;padding:9px 10px;border-radius:6px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:.95rem">
   <option value="">All models</option>
  </select>
 </div>
 <button class="btn btn-primary" onclick="copyExport()" id="copy-btn" style="width:auto;padding:12px 20px;margin:0;flex-shrink:0">Copy</button>
</div>
<div class="stats" id="matrix-stats"></div>
<pre class="log" id="matrix-out" style="max-height:none;white-space:pre;overflow-x:auto;font-size:.7rem;margin-top:10px"></pre>
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

// Scrape (combined search + individual URLs)
async function startScrape(){
 const searchUrl=v('s-url');
 const rawUrls=document.getElementById('add-urls').value;
 const urls=rawUrls.split('\n').map(s=>s.trim()).filter(s=>s.startsWith('http'));
 if(!searchUrl&&!urls.length){alert('Paste a search URL, individual listing URLs, or both.');return;}
 const p={
  search_url:searchUrl||'',
  urls:urls,
  pages:n('s-pages')||3
 };
 document.getElementById('scrape-btn').disabled=true;
 document.getElementById('scrape-progress').style.display='block';
 try{
  const r=await(await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(r.error){alert(r.error);document.getElementById('scrape-btn').disabled=false;return;}
  pollTimer=setInterval(pollProgress,1500);
 }catch(e){alert(e);document.getElementById('scrape-btn').disabled=false;}
}

async function pollProgress(){
 const r=await(await fetch('/api/search/status')).json();
 const pct=r.total?Math.round(r.progress/r.total*100):0;
 document.getElementById('pbar').style.width=pct+'%';
 document.getElementById('ptext').textContent=r.status;
 document.getElementById('plog').textContent=r.log.join('\n');
 document.getElementById('plog').scrollTop=9999;
 if(!r.running){
  clearInterval(pollTimer);
  document.getElementById('scrape-btn').disabled=false;
  await loadCars();
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(x=>x.classList.remove('active'));
  document.querySelector('[data-t="matrix"]').classList.add('active');
  document.getElementById('matrix').classList.add('active');
  renderMatrix();
 }
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
   <span class="meta"> · ${fmtKm(c.mileage_km)} · ${esc(str(c.body_color))} · ${esc(str(c.fuel_type))}</span>
   <span class="feat-count">${fc} features</span>
   ${c.url?`<div style="margin-top:6px"><a href="${esc(c.url)}" target="_blank" rel="noopener" style="color:var(--blue);font-size:.8rem;text-decoration:none">View on AutoScout24 ↗</a></div>`:''}
   ${fc?`<details style="margin-top:6px"><summary style="font-size:.75rem;color:var(--muted);cursor:pointer">Show ${fc} features</summary>
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

// ═══ EXPORT FOR CLAUDE ═══
let exportText='';

function populateModelFilter(){
 const sel=document.getElementById('model-filter');
 const cur=sel.value;
 const models=new Set();
 cars.forEach(c=>{
  const m=[c.make,c.model].filter(Boolean).join(' ');
  if(m)models.add(m);
 });
 sel.innerHTML='<option value="">All models ('+cars.length+' cars)</option>';
 [...models].sort().forEach(m=>{
  const count=cars.filter(c=>[c.make,c.model].filter(Boolean).join(' ')===m).length;
  sel.innerHTML+=`<option value="${esc(m)}">${esc(m)} (${count})</option>`;
 });
 if(cur)sel.value=cur;
}

function renderMatrix(){
 const out=document.getElementById('matrix-out');
 const statsEl=document.getElementById('matrix-stats');
 const modelFilter=document.getElementById('model-filter').value;

 populateModelFilter();

 if(cars.length<1){out.textContent='No cars yet. Scrape some listings first.';statsEl.innerHTML='';exportText='';return;}

 // Filter by model
 let filtered=cars;
 if(modelFilter){
  filtered=cars.filter(c=>[c.make,c.model].filter(Boolean).join(' ')===modelFilter);
 }

 if(!filtered.length){out.textContent='No cars match this filter.';statsEl.innerHTML='';exportText='';return;}

 // Sort by most features first
 const sorted=[...filtered].sort((a,b)=>(b.features||[]).length-(a.features||[]).length);

 // Collect all features
 const carFeats=sorted.map(c=>new Set(c.features||[]));
 const allFeats=new Set();
 carFeats.forEach(s=>s.forEach(f=>allFeats.add(f)));
 const feats=[...allFeats].sort();

 if(!feats.length){out.textContent='No feature data. Scrape listings to get features.';statsEl.innerHTML='';exportText='';return;}

 // Detect model for the prompt
 const modelName=modelFilter||[...new Set(sorted.map(c=>[c.make,c.model].filter(Boolean).join(' ')))].join(' / ');

 // Stats
 statsEl.innerHTML=`
  <div class="stat"><div class="val">${sorted.length}</div><div class="lbl">Cars</div></div>
  <div class="stat"><div class="val">${feats.length}</div><div class="lbl">Features</div></div>
 `;

 // Build export text — COMPACT FORMAT
 // Instead of a giant NxM grid, list standard features once,
 // then per-car only show what's different
 let lines=[];

 // Classify features
 const common=feats.filter(f=>carFeats.every(s=>s.has(f)));
 const varying=feats.filter(f=>!carFeats.every(s=>s.has(f)));

 lines.push(`=== AUTOSCOUT24 SCRAPED DATA ===`);
 lines.push(`Model: ${modelName}`);
 lines.push(`${sorted.length} cars | ${feats.length} unique features | ${common.length} standard | ${varying.length} differ`);

 // Per-car block: specs + only the VARYING features this car HAS
 lines.push('');
 lines.push('=== CARS (sorted by most features) ===');
 sorted.forEach((c,i)=>{
  const label=[c.make,c.model,c.version].filter(Boolean).join(' ')||c.title||'?';
  const extras=varying.filter(f=>carFeats[i].has(f));
  const missing=varying.filter(f=>!carFeats[i].has(f));
  lines.push('');
  lines.push(`[Car ${i+1}] ${label}`);
  const parts=[];
  if(c.price) parts.push(`€${typeof c.price==='number'?c.price.toLocaleString('de-DE'):c.price}`);
  if(c.mileage_km) parts.push(`${typeof c.mileage_km==='number'?c.mileage_km.toLocaleString('de-DE'):c.mileage_km} km`);
  if(c.first_registration) parts.push(`Reg: ${str(c.first_registration)}`);
  if(c.body_color) parts.push(str(c.body_color));
  if(c.fuel_type) parts.push(str(c.fuel_type));
  if(c.power_hp) parts.push(`${str(c.power_hp)} HP`);
  if(c.transmission) parts.push(str(c.transmission));
  lines.push(parts.join(' | '));
  if(c.url) lines.push(c.url);
  if(c.seller_name) lines.push(`Seller: ${str(c.seller_name)}, ${str(c.seller_city)} ${str(c.seller_country)}`);
  lines.push(`Has (${extras.length}/${varying.length} optional): ${extras.join(', ')}`);
  lines.push(`Missing (${missing.length}): ${missing.join(', ')}`);
 });

 // Standard features (all cars have these — listed once)
 if(common.length){
  lines.push('');
  lines.push(`=== STANDARD FEATURES (all ${sorted.length} cars have these — ${common.length} features) ===`);
  lines.push(common.join(', '));
 }

 exportText=lines.join('\n');
 out.textContent=exportText;
}

function copyExport(){
 if(!exportText){alert('Nothing to copy. Scrape some cars first.');return;}
 navigator.clipboard.writeText(exportText).then(()=>{
  const btn=document.getElementById('copy-btn');
  btn.textContent='Copied!';
  btn.style.background='var(--green)';
  setTimeout(()=>{btn.textContent='Copy to Clipboard';btn.style.background='';},2000);
 }).catch(()=>{
  // Fallback for non-HTTPS
  const ta=document.createElement('textarea');
  ta.value=exportText;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
  const btn=document.getElementById('copy-btn');
  btn.textContent='Copied!';
  btn.style.background='var(--green)';
  setTimeout(()=>{btn.textContent='Copy to Clipboard';btn.style.background='';},2000);
 });
}

// Helpers
function v(id){return document.getElementById(id).value.trim();}
function n(id){const x=v(id);return x?Number(x):null;}
function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function fmtP(v){return typeof v==='number'?'€'+v.toLocaleString():(typeof v==='string'&&v?v:'—');}
function fmtKm(v){return typeof v==='number'?v.toLocaleString()+' km':(typeof v==='string'&&v?v:'—');}
function str(v){if(v==null)return'—';if(typeof v==='object')return v.label||v.name||v.value||v.formatted||'—';return ''+v||'—';}

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
