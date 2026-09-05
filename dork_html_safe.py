from flask import Flask, render_template_string, jsonify, request
import json
import threading
import time
import random
import hashlib
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

### =========================
### Configuration & State
### =========================
SEARXNG_URL = "http://localhost:8080"
GOOGLE_ENGINE = "google cse"
DORK_FILE = Path("my_dorks.txt")
RESULTS_FILE = Path("results.txt")
STATE_FILE = Path("worker_state.json")
CACHE_FILE = Path("worker_cache.json")
PROXY_FILE = Path("proxies.txt")

DEFAULT_PAGES = 2
DEFAULT_PAGE_GAP = 20
DEFAULT_QUERY_MIN = 20
DEFAULT_QUERY_MAX = 30
MIN_PAGE_GAP = 30
MIN_QUERY_DELAY = 60
MAX_PAGES = 50
MAX_LOGS = 300

# Spectacular expanded pool of 25+ rotating modern User Agents to bypass Google CSE tracking signatures
USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome on MacOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Firefox on Windows & Linux
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Safari on MacOS & iOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Mobile/15E148 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/605.1.15",
    "Mozilla/5.0 (iPad; CPU OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/605.1.15",
    # Edge on Windows & Mac
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    # Opera on Windows & Android
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.0.0",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile OPR/79.0.2254"
]

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

state = load_json(STATE_FILE, {
    "index": 0,
    "page": 1,
    "status": "PAUSED",
    "action": "IDLE",
    "next_action": "",
    "last_query": "",
    "last_error": "",
    "last_page_urls": 0,
    "pages": DEFAULT_PAGES,
    "page_gap": DEFAULT_PAGE_GAP,
    "query_min": DEFAULT_QUERY_MIN,
    "query_max": DEFAULT_QUERY_MAX,
    "urls": 0,
    "session_new_urls": 0,
    "consecutive_empty": 0,
    "selected_proxy": "",
    "proxy_status": "NOT CONFIGURED",
    "proxy_error": "",
    "logs": []
})

state.setdefault("logs", [])
state.setdefault("action", "IDLE")
state.setdefault("next_action", "")
state.setdefault("page_gap", DEFAULT_PAGE_GAP)
state.setdefault("query_min", DEFAULT_QUERY_MIN)
state.setdefault("query_max", DEFAULT_QUERY_MAX)
state["page_gap"] = max(MIN_PAGE_GAP, float(state["page_gap"]))
state["query_min"] = max(MIN_QUERY_DELAY, float(state["query_min"]))
state["query_max"] = max(state["query_min"], float(state["query_max"]))
state.setdefault("consecutive_empty", 0)

cache = load_json(CACHE_FILE, {})
state_lock = threading.Lock()
worker_thread = None
stop_event = threading.Event()
pause_event = threading.Event()
pause_event.clear()

def save_state():
    with state_lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)

def save_cache():
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)

def load_dorks():
    if not DORK_FILE.exists():
        return []
    return [x.strip() for x in DORK_FILE.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip()]

def existing_urls():
    if not RESULTS_FILE.exists():
        return set()
    result = set()
    with RESULTS_FILE.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith(("http://", "https://")):
                result.add(line)
    return result

def set_action(action, next_action=""):
    state["action"] = action
    state["next_action"] = next_action
    save_state()

def add_log(index, query, page, urls, new_urls, total_urls, status, detail=""):
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "index": index,
        "query": query,
        "page": page,
        "urls": urls,
        "new_urls": new_urls,
        "total_urls": total_urls,
        "status": status
    }
    if detail:
        entry["detail"] = detail
    state["logs"].append(entry)
    state["logs"] = state["logs"][-MAX_LOGS:]
    save_state()

### =========================
### Fixed Proxy Support
### =========================
def load_proxies():
    if not PROXY_FILE.exists():
        return []
    out = []
    for line in PROXY_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = "http://" + line
        out.append(line)
    return list(dict.fromkeys(out))

def proxy_for_session():
    proxies = load_proxies()
    if not proxies:
        return None
    selected = state.get("selected_proxy", "")
    if selected not in proxies:
        return None
    return selected

def apply_proxy(session):
    proxy = proxy_for_session()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    else:
        session.proxies.clear()
    return proxy

### =========================
### SearXNG / Google CSE
### =========================
def extract_urls(html):
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for article in soup.select("article.result"):
        a = article.select_one("h3 a[href]") or article.select_one("a.url_header[href]")
        if a:
            u = a.get("href", "").strip()
            if u.startswith(("http://", "https://")):
                urls.append(u)
    if not urls:
        for a in soup.select("h3 a[href]"):
            u = a.get("href", "").strip()
            if u.startswith(("http://", "https://")):
                urls.append(u)
    return list(dict.fromkeys(urls))

def search_page(session, query, page):
    params = {
        "q": query,
        "categories": "general",
        "engines": GOOGLE_ENGINE,
        "pageno": page,
        "language": "en",
        "safesearch": "0",
        "format": "html"
    }
    headers = {"User-Agent": random.choice(USER_AGENTS)}  # User-Agent rotation per search!
    response = session.get(f"{SEARXNG_URL}/search", params=params, headers=headers, timeout=30)
    
    if response.status_code in (403, 429, 503):
        raise RuntimeError(f"HTTP {response.status_code}")
        
    response.raise_for_status()
    return extract_urls(response.text), response.elapsed.total_seconds()

def google_cse_status():
    try:
        r = requests.get(f"{SEARXNG_URL}/stats", headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.find_all("tr"):
            text = " ".join(row.get_text(" ", strip=True).split())
            low = text.lower()
            if "google cse" in low:
                if any(x in low for x in ("suspended", "captcha", "access denied", "too many requests", "timeout")):
                    return "SUSPENDED / BLOCKED", text
                return "OK", text
        return "NOT FOUND", "google cse was not found in SearXNG stats"
    except Exception as e:
        return "UNKNOWN", str(e)

def inspect_empty_result():
    status, detail = google_cse_status()
    if status == "OK":
        return "NO RESULTS", detail
    if status == "SUSPENDED / BLOCKED":
        return "GOOGLE CSE SUSPENDED", detail
    return "GOOGLE CSE STATUS UNKNOWN", detail

### =========================
### Crawler Worker
### =========================
def worker():
    global cache
    dorks = load_dorks()
    session = requests.Session()
    apply_proxy(session)
    urls_seen = existing_urls()
    
    state["urls"] = len(urls_seen)
    save_state()
    
    while not stop_event.is_set():
        pause_event.wait()
        
        if stop_event.is_set():
            return
            
        if state["index"] >= len(dorks):
            state["status"] = "FINISHED"
            state["last_query"] = ""
            set_action("FINISHED", "All queries completed")
            return
            
        query_number = state["index"] + 1
        query = dorks[state["index"]]
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        
        state["status"] = "RUNNING"
        state["last_query"] = query
        state["page"] = 1
        state["last_error"] = ""
        state["consecutive_empty"] = 0
        save_state()
        
        query_total = 0
        query_new = 0
        pages_done = 0
        
        for page in range(1, int(state["pages"]) + 1):
            if stop_event.is_set():
                return
                
            pause_event.wait()
            key = f"{query_hash}:{page}"
            
            # Use local cache if valid
            if key in cache and cache[key].get("complete") is True:
                cached_urls = cache[key].get("urls", [])
                cached_new = 0
                for u in cached_urls:
                    if u not in urls_seen:
                        with RESULTS_FILE.open("a", encoding="utf-8") as f:
                            f.write(u + "\n")
                        urls_seen.add(u)
                        cached_new += 1
                        
                query_total += len(cached_urls)
                query_new += cached_new
                pages_done += 1
                
                state["page"] = page
                state["last_page_urls"] = len(cached_urls)
                state["urls"] = len(urls_seen)
                
                add_log(query_number, query, page, len(cached_urls), cached_new, query_total, "CACHED")
                set_action(f"PAGE {page}/{state['pages']} CACHED", "Continuing pagination")
                continue
                
            # Perform preflight check
            health, detail = google_cse_status()
            if health != "OK":
                state["last_error"] = detail
                state["status"] = "QUARANTINED"  # Trigger quarantine if Google CSE is suspended/blocked
                add_log(query_number, query, page, 0, 0, query_total, "QUARANTINED", detail)
                set_action("QUARANTINED", "Google CSE preflight failed • Pausing worker under Quarantine")
                pause_event.clear()
                save_state()
                return
                
            set_action(f"REQUESTING PAGE {page}/{state['pages']}", "Google CSE preflight OK • Sending query request")
            state["page"] = page
            save_state()
            
            try:
                urls, elapsed = search_page(session, query, page)
            except Exception as e:
                health, detail = google_cse_status()
                state["last_error"] = str(e)
                state["status"] = "QUARANTINED"  # Immediate quarantine on error/timeout
                status_label = "QUARANTINED"
                add_log(query_number, query, page, 0, 0, query_total, status_label, detail or str(e))
                set_action("QUARANTINED", f"Request failed: {str(e)} • Quarantining node")
                pause_event.clear()
                save_state()
                return
                
            # Immediate Quarantine if 0 URLs are found (Google CSE Suspension Suspected)
            if not urls:
                status, detail = inspect_empty_result()
                state["last_error"] = f"Zero results returned from SearXNG. Preflight status: {status} ({detail})"
                state["status"] = "QUARANTINED"  # Immediate Quarantine on empty results!
                add_log(query_number, query, page, 0, 0, query_total, "QUARANTINED", state["last_error"])
                set_action("QUARANTINED", "0 URLs found • Quarantining node to protect IP from Google CSE blocks")
                pause_event.clear()
                save_state()
                return
                
            # Save results immediately
            new_urls = 0
            with RESULTS_FILE.open("a", encoding="utf-8") as f:
                for u in urls:
                    if u not in urls_seen:
                        f.write(u + "\n")
                        urls_seen.add(u)
                        new_urls += 1
                        
            query_total += len(urls)
            query_new += new_urls
            pages_done += 1
            
            state["urls"] = len(urls_seen)
            state["session_new_urls"] += new_urls
            state["last_page_urls"] = len(urls)
            state["page"] = page
            
            cache[key] = {"complete": True, "urls": urls, "saved_at": time.time()}
            save_cache()
            
            add_log(query_number, query, page, len(urls), new_urls, query_total, "OK", f"Response {elapsed:.1f}s")
            set_action(f"PAGE {page}/{state['pages']} COMPLETE • {len(urls)} URLs", f"Requesting page {page+1}/{state['pages']}" if page < int(state["pages"]) else "Query complete")
            save_state()
            
            if page < int(state["pages"]):
                for remaining in range(int(max(1, state["page_gap"])), 0, -1):
                    if stop_event.is_set():
                        return
                    pause_event.wait()
                    set_action(f"PAGE {page} COMPLETE • {len(urls)} URLs", f"Next page in {remaining}s")
                    time.sleep(1)
                    
        add_log(query_number, query, 0, query_total, query_new, query_total, "TOTAL", f"{pages_done}/{state['pages']} pages processed")
        state["index"] += 1
        state["page"] = 1
        save_state()
        
        qmin = float(state["query_min"])
        qmax = float(state["query_max"])
        delay = random.uniform(qmin, qmax)
        for remaining in range(max(1, int(delay)), 0, -1):
            if stop_event.is_set():
                return
            pause_event.wait()
            set_action("QUERY COMPLETE", f"Next query in {remaining}s")
            time.sleep(1)

### =========================
### Web UI Endpoints
### =========================
HTML_UI = r"""
<!doctype html>
<html>
<head><title>Dork Matrix Worker Node</title></head>
<body style="background:#090d16; color:#38bdf8; font-family:sans-serif; text-align:center; padding-top:10%;">
  <h1>📡 Dork Worker Node Active</h1>
  <p>Status: <strong style="color:lime;">OK</strong></p>
  <p>Port: <strong>5000</strong></p>
  <p>This node is controlled asynchronously by the central Master Dashboard.</p>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_UI)

@app.route("/status")
def status():
    dorks = load_dorks()
    actual = len(existing_urls())
    state["urls"] = actual
    engine_status, engine_detail = google_cse_status()
    return jsonify({
        **state,
        "total": len(dorks),
        "worker_alive": worker_thread is not None and worker_thread.is_alive(),
        "proxy_file": str(PROXY_FILE),
        "proxy_count": len(load_proxies()),
        "selected_proxy": state.get("selected_proxy", ""),
        "proxy_status": state.get("proxy_status", "NOT CONFIGURED"),
        "proxy_error": state.get("proxy_error", ""),
        "engine_status": engine_status,
        "engine_detail": engine_detail
    })

@app.route("/logs")
def logs():
    return jsonify(state.get("logs", [])[-MAX_LOGS:])

@app.route("/results")
def results():
    if not RESULTS_FILE.exists():
        return jsonify([])
    lines = RESULTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    urls = [x.strip() for x in lines if x.strip().startswith(("http://", "https://"))]
    return jsonify(list(dict.fromkeys(reversed(urls)))[:100])

@app.route("/start", methods=["POST"])
def start():
    global worker_thread
    data = request.get_json(silent=True) or {}
    
    state["pages"] = max(1, min(MAX_PAGES, int(data.get("pages", state["pages"]))))
    state["page_gap"] = max(MIN_PAGE_GAP, float(data.get("page_gap", state["page_gap"])))
    state["query_min"] = max(MIN_QUERY_DELAY, float(data.get("min_delay", state["query_min"])))
    state["query_max"] = max(state["query_min"], float(data.get("max_delay", state["query_max"])))
    
    stop_event.clear()
    pause_event.set()
    state["status"] = "RUNNING"
    state["last_error"] = ""
    state["proxy_status"] = "SELECTED" if state.get("selected_proxy") else "DIRECT"
    state["proxy_error"] = ""
    set_action("STARTING", "Running preflight health check")
    save_state()
    
    if worker_thread is None or not worker_thread.is_alive():
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
    return jsonify({"ok": True})

@app.route("/pause", methods=["POST"])
def pause():
    pause_event.clear()
    state["status"] = "PAUSED"
    set_action("PAUSED", "Worker paused manually")
    save_state()
    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop():
    stop_event.set()
    pause_event.set()
    state["status"] = "STOPPED"
    set_action("STOPPED", "Crawl loop terminated safely")
    save_state()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
