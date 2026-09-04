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

# =========================
# Configuration
# =========================
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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)

# =========================
# Persistent state
# =========================
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


state = load_json(STATE_FILE, {
    "index": 0,                 # number of fully completed queries
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
        tmp.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        tmp.replace(STATE_FILE)


def save_cache():
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(cache, ensure_ascii=False),
        encoding="utf-8"
    )
    tmp.replace(CACHE_FILE)


def load_dorks():
    if not DORK_FILE.exists():
        return []
    return [
        x.strip()
        for x in DORK_FILE.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()
        if x.strip()
    ]


def existing_urls():
    if not RESULTS_FILE.exists():
        return set()

    result = set()
    with RESULTS_FILE.open(
        "r", encoding="utf-8", errors="ignore"
    ) as f:
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


# =========================
# Fixed proxy support
# =========================
def load_proxies():
    """Load proxies from proxies.txt; one proxy per line."""
    if not PROXY_FILE.exists():
        return []
    out = []
    for line in PROXY_FILE.read_text(
        encoding="utf-8", errors="ignore"
    ).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = "http://" + line
        out.append(line)
    return list(dict.fromkeys(out))


def proxy_for_session():
    """Use one manually selected proxy for the whole worker session."""
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
        session.proxies.update({
            "http": proxy,
            "https": proxy
        })
    else:
        session.proxies.clear()
    return proxy


# =========================
# SearXNG / Google CSE
# =========================
def extract_urls(html):
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    for article in soup.select("article.result"):
        a = (
            article.select_one("h3 a[href]")
            or article.select_one("a.url_header[href]")
        )
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

    response = session.get(
        f"{SEARXNG_URL}/search",
        params=params,
        headers={"User-Agent": UA},
        timeout=30
    )

    if response.status_code in (403, 429, 503):
        raise RuntimeError(f"HTTP {response.status_code}")

    response.raise_for_status()
    return extract_urls(response.text), response.elapsed.total_seconds()


def google_cse_status():
    """Inspect only the exact configured Google CSE row."""
    try:
        r = requests.get(
            f"{SEARXNG_URL}/stats",
            headers={"User-Agent": UA},
            timeout=10
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for row in soup.find_all("tr"):
            text = " ".join(row.get_text(" ", strip=True).split())
            low = text.lower()

            if "google cse" in low:
                if any(x in low for x in (
                    "suspended",
                    "captcha",
                    "access denied",
                    "too many requests",
                    "timeout"
                )):
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


# =========================
# Worker
# =========================
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
        query_hash = hashlib.sha256(
            query.encode("utf-8")
        ).hexdigest()

        state["status"] = "RUNNING"
        state["last_query"] = query
        state["page"] = 1
        state["last_error"] = ""
        state["consecutive_empty"] = 0
        save_state()

        query_total = 0
        query_new = 0
        pages_done = 0

        # Always explicitly attempt every configured page.
        for page in range(1, int(state["pages"]) + 1):
            if stop_event.is_set():
                return

            pause_event.wait()

            key = f"{query_hash}:{page}"

            # Cache is only used for pages that were actually completed.
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

                add_log(
                    query_number, query, page,
                    len(cached_urls), cached_new,
                    query_total, "CACHED"
                )
                set_action(
                    f"PAGE {page}/{state['pages']} CACHED",
                    "Continuing pagination"
                )
                continue

            # Fresh preflight: do not send another search while Google CSE is
            # reported suspended/captcha/blocked by SearXNG.
            health, detail = google_cse_status()
            if health != "OK":
                state["last_error"] = detail
                state["status"] = "AUTO-PAUSED"
                add_log(
                    query_number, query, page, 0, 0, query_total,
                    "GOOGLE CSE SUSPENDED" if health == "SUSPENDED / BLOCKED" else "GOOGLE CSE STATUS UNKNOWN",
                    detail
                )
                set_action(
                    "GOOGLE CSE SUSPENDED" if health == "SUSPENDED / BLOCKED" else "GOOGLE CSE STATUS UNKNOWN",
                    "Resume manually after SearXNG reports Google CSE OK"
                )
                pause_event.clear()
                save_state()
                return

            set_action(
                f"REQUESTING PAGE {page}/{state['pages']}",
                "Google CSE preflight OK • sending one request"
            )
            state["page"] = page
            save_state()

            try:
                urls, elapsed = search_page(
                    session, query, page
                )
            except Exception as e:
                health, detail = google_cse_status()

                state["last_error"] = str(e)
                state["status"] = "AUTO-PAUSED"

                status = (
                    "GOOGLE CSE SUSPENDED"
                    if health == "SUSPENDED / BLOCKED"
                    else "REQUEST ERROR"
                )

                add_log(
                    query_number, query, page,
                    0, 0, query_total,
                    status,
                    detail or str(e)
                )

                set_action(
                    status,
                    "Press RESUME when the service is available"
                )
                pause_event.clear()
                save_state()
                return

            if not urls:
                status, detail = inspect_empty_result()

                # Do not cache a zero-result page if the engine is not healthy.
                if status != "NO RESULTS":
                    state["last_error"] = detail
                    state["status"] = "AUTO-PAUSED"

                    add_log(
                        query_number, query, page,
                        0, 0, query_total,
                        status, detail
                    )

                    set_action(
                        status,
                        "Press RESUME when Google CSE is available"
                    )
                    pause_event.clear()
                    save_state()
                    return

                cache[key] = {
                    "complete": True,
                    "urls": [],
                    "saved_at": time.time()
                }
                save_cache()

                state["last_page_urls"] = 0
                state["page"] = page
                pages_done += 1

                add_log(
                    query_number, query, page,
                    0, 0, query_total,
                    "NO RESULTS",
                    f"Response {detail}"
                )

                set_action(
                    f"PAGE {page}/{state['pages']} • 0 RESULTS",
                    (
                        f"Requesting page {page + 1}/{state['pages']}"
                        if page < int(state["pages"])
                        else "Query complete"
                    )
                )
                save_state()

                # Crucial: DO NOT break here.
                if page < int(state["pages"]):
                    for remaining in range(
                        int(max(1, state["page_gap"])), 0, -1
                    ):
                        if stop_event.is_set():
                            return
                        pause_event.wait()
                        set_action(
                            f"PAGE {page} EMPTY",
                            f"Next page in {remaining}s"
                        )
                        time.sleep(1)
                    continue

                break

            # Save all page URLs immediately.
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

            cache[key] = {
                "complete": True,
                "urls": urls,
                "saved_at": time.time()
            }
            save_cache()

            add_log(
                query_number, query, page,
                len(urls), new_urls,
                query_total, "OK",
                f"Response {elapsed:.1f}s"
            )

            set_action(
                f"PAGE {page}/{state['pages']} COMPLETE • "
                f"{len(urls)} URLs",
                (
                    f"Requesting page {page + 1}/{state['pages']}"
                    if page < int(state["pages"])
                    else "Query complete"
                )
            )
            save_state()

            if page < int(state["pages"]):
                for remaining in range(
                    int(max(1, state["page_gap"])), 0, -1
                ):
                    if stop_event.is_set():
                        return
                    pause_event.wait()
                    set_action(
                        f"PAGE {page} COMPLETE • {len(urls)} URLs",
                        f"Next page in {remaining}s"
                    )
                    time.sleep(1)

        # Query is complete only after all requested pages were processed.
        add_log(
            query_number, query, 0,
            query_total, query_new,
            query_total, "TOTAL",
            f"{pages_done}/{state['pages']} pages processed"
        )

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
            set_action(
                "QUERY COMPLETE",
                f"Next query in {remaining}s"
            )
            time.sleep(1)


# =========================
# Web UI
# =========================
HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Google Search Worker V8</title>
<style>
*{box-sizing:border-box}
body{
 margin:0;min-height:100vh;color:#edf4ff;
 font-family:Inter,Segoe UI,Arial,sans-serif;
 background:
 radial-gradient(circle at 8% 5%,#263f82 0,transparent 31%),
 radial-gradient(circle at 92% 8%,#0b5147 0,transparent 29%),
 linear-gradient(135deg,#070b14,#0b1220 55%,#071513);
}
.wrap{max-width:1200px;margin:auto;padding:28px 18px 50px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
h1{margin:0;font-size:32px}
.sub{color:#93a7c7;margin-top:5px}
.badge{border:1px solid #334155;border-radius:999px;padding:10px 16px;font-weight:800}
.card{
 background:rgba(13,21,36,.82);border:1px solid rgba(148,163,184,.16);
 border-radius:20px;padding:20px;margin:14px 0;
 box-shadow:0 18px 55px rgba(0,0,0,.25);backdrop-filter:blur(16px)
}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.metric{background:#091120;border:1px solid #20304a;border-radius:15px;padding:16px}
.label{font-size:12px;color:#91a2bd;text-transform:uppercase}
.value{font-size:24px;font-weight:800;margin-top:7px}
.progress{height:13px;background:#09101e;border-radius:99px;overflow:hidden;margin-top:18px}
.bar{height:100%;width:0;background:linear-gradient(90deg,#38bdf8,#4ade80);transition:width .4s}
.small{font-size:13px;color:#91a2bd}
.current,.action{
 margin-top:10px;background:#081120;border:1px solid #1d2c44;
 border-radius:12px;padding:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap
}
.health{margin-top:10px;display:flex;align-items:center;gap:9px;color:#aebbd0}
.dot{width:10px;height:10px;border-radius:50%;background:#64748b}
.controls{display:flex;gap:10px;flex-wrap:wrap}
button{border:0;border-radius:11px;padding:12px 19px;font-weight:800;cursor:pointer}
.resume{background:#4ade80}.pause{background:#facc15}.stop{background:#fb7185}
button:hover{filter:brightness(1.08)}
.settings{display:flex;gap:20px;flex-wrap:wrap;margin-top:16px;align-items:center}
input{width:72px;background:#091120;color:white;border:1px solid #334155;border-radius:9px;padding:9px}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.logs{max-height:440px;overflow:auto;border:1px solid #1e2b40;border-radius:13px;background:#060b14}
.log{
 display:grid;grid-template-columns:60px 1fr 58px 82px 78px 150px;
 gap:10px;padding:10px 12px;border-bottom:1px solid #152033;
 font-size:12px;align-items:center
}
.q{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ok{color:#4ade80}.warn{color:#facc15}.bad{color:#fb7185}.cache{color:#93c5fd}
.results{max-height:330px;overflow:auto;border:1px solid #1e2b40;border-radius:13px;background:#060b14}
.result{padding:10px 13px;border-bottom:1px solid #152033;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result a{color:#7dd3fc;text-decoration:none;font-size:13px}
.result a:hover{text-decoration:underline}
@media(max-width:850px){
 .grid{grid-template-columns:repeat(2,1fr)}
 .log{grid-template-columns:45px 1fr 50px 65px 65px 110px}
}
@media(max-width:520px){.grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column;gap:12px}}
</style>
</head>
<body>
<div class="wrap">

<div class="top">
 <div>
  <h1>Google Search Worker <span style="font-size:14px;color:#64748b">V11</span></h1>
  <div class="sub">Persistent SearXNG • Google CSE only • conservative rate mode</div>
 </div>
 <div id="badge" class="badge">PAUSED</div>
</div>

<div class="card">
 <div class="grid">
  <div class="metric"><div class="label">Queries</div><div id="q" class="value">0 / 0</div></div>
  <div class="metric"><div class="label">URLs collected</div><div id="urls" class="value">0</div></div>
  <div class="metric"><div class="label">Current page</div><div id="page" class="value">1 / 2</div></div>
  <div class="metric"><div class="label">Pages / query</div><div id="pagesv" class="value">2</div></div>
 </div>

 <div class="progress"><div id="bar" class="bar"></div></div>
 <div id="pct" class="small" style="margin-top:7px">0%</div>
 <div id="current" class="current">No active query</div>
 <div id="action" class="action">Worker: IDLE</div>
 <div class="health"><span id="dot" class="dot"></span><span id="health">Google CSE: checking...</span></div>
</div>

<div class="card">
 <div class="head"><b>Network / Proxy</b><span class="small">one fixed proxy for the session</span></div>
 <div class="settings">
  <label>Proxy
   <select id="proxy" style="background:#091120;color:#fff;border:1px solid #334155;border-radius:9px;padding:9px;min-width:230px">
    <option value="">Direct connection</option>
   </select>
  </label>
  <button onclick="selectProxy()">SELECT</button>
  <button onclick="testProxy()">TEST CONNECTION</button>
  <span id="proxyStatus" class="small">Not configured</span>
 </div>
 <div class="small" style="margin-top:10px">
  Add one or more proxies to <b>proxies.txt</b>, one per line. The worker uses
  the selected proxy consistently; it does not automatically rotate proxies.
 </div>
</div>

<div class="card">
 <div class="controls">
  <button class="resume" onclick="start()">▶ RESUME</button>
  <button class="pause" onclick="pause()">Ⅱ PAUSE</button>
  <button class="stop" onclick="stop()">■ STOP</button>
 </div>
 <div class="settings">
  <span>Engine: <b>Google CSE</b></span>
  <label>Pages/query <input id="pages" type="number" min="1" max="50" value="2"></label>
  <label>Page gap <input id="pagegap" type="number" min="1" value="30"> sec</label>
  <label>Query delay <input id="min" type="number" min="1" value="60"> - <input id="max" type="number" min="1" value="30"> sec</label>
 </div>
 <div class="small" style="margin-top:12px">🛡 Custom pacing: the values below are used exactly (subject to a 1s minimum). If SearXNG reports Google CSE suspended/blocked, the worker stops automatically.</div>
</div>

<div class="card">
 <div class="head"><b>Search log</b><span class="small">PAGE / URLs / NEW / STATUS</span></div>
 <div id="logs" class="logs"><div class="result small">No activity yet.</div></div>
</div>

<div class="card">
 <div class="head"><b>Latest collected URLs</b><span class="small">latest 100</span></div>
 <div id="results" class="results"><div class="result small">No URLs yet.</div></div>
</div>

<div class="small">All state, cache and results are stored locally. You can shut down the PC and resume later.</div>
</div>

<script>
async function post(path,body=null){
 const o={method:"POST"};
 if(body){o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(body)}
 return fetch(path,o);
}
async function loadProxies(){
 try{
  const list=await (await fetch("/proxy/list")).json();
  proxy.innerHTML='<option value="">Direct connection</option>'+
   list.map(x=>'<option value="'+esc(x.value)+'">'+esc(x.label)+'</option>').join("");
 }catch(e){}
}
async function selectProxy(){
 const r=await post("/proxy/select",{proxy:proxy.value});
 const j=await r.json();
 if(!j.ok) alert(j.error||"Could not select proxy");
 else update();
}
async function testProxy(){
 const r=await post("/proxy/test");
 const j=await r.json();
 proxyStatus.innerText=(j.status||"UNKNOWN")+" • "+(j.detail||"");
 update();
}

function markDirty(el){ el.dataset.dirty="1"; }
["pagegap","min","max","pages"].forEach(id=>{
 const el=document.getElementById(id);
 if(el) el.addEventListener("input",()=>markDirty(el));
});

async function start(){
 await post("/start",{
  pages:+pages.value,
  page_gap:+pagegap.value,
  min_delay:+min.value,
  max_delay:+max.value
 });
 ["pagegap","min","max","pages"].forEach(id=>{
   const el=document.getElementById(id);
   if(el) delete el.dataset.dirty;
 });
}
async function pause(){await post("/pause")}
async function stop(){await post("/stop")}

async function update(){
 try{
  const s=await (await fetch("/status")).json();
  const pct=s.total ? s.index/s.total*100 : 0;
  badge.innerText=s.status;
  q.innerText=s.index.toLocaleString()+" / "+s.total.toLocaleString();
  urls.innerText=s.urls.toLocaleString();
  page.innerText=s.page+" / "+s.pages;
  pagesv.innerText=s.pages;
  // Do not overwrite a field while the user is editing it.
  // This fixes values such as 20 being changed back to the saved 30.
  if(document.activeElement!==pagegap && !pagegap.dataset.dirty)
    pagegap.value=s.page_gap;
  if(document.activeElement!==min && !min.dataset.dirty)
    min.value=s.query_min;
  if(document.activeElement!==max && !max.dataset.dirty)
    max.value=s.query_max;
  bar.style.width=Math.min(100,pct)+"%";
  document.getElementById("pct").innerText=pct.toFixed(4)+"%";
  current.innerText=s.last_query ? "Current: "+s.last_query : "No active query";
  proxyStatus.innerText=(s.proxy_status||"NOT CONFIGURED")+
   (s.proxy_error ? " • "+s.proxy_error : "");
  if(s.selected_proxy && proxy.value!==s.selected_proxy) proxy.value=s.selected_proxy;

  action.innerText="Worker: "+(s.action||"IDLE")+
   (s.next_action ? " • "+s.next_action : "")+
   " • thread "+(s.worker_alive?"ALIVE":"STOPPED");

  if(s.engine_status==="SUSPENDED / BLOCKED"){
   health.innerText="Google CSE: SUSPENDED / BLOCKED • "+(s.engine_detail||"");
   dot.style.background="#fb7185";
  }else if(s.engine_status==="UNKNOWN" || s.engine_status==="NOT FOUND"){
   health.innerText="Google CSE: "+s.engine_status+" • "+(s.engine_detail||"");
   dot.style.background="#fb7185";
  }else{
   health.innerText="Google CSE: responding / no throttle detected";
   dot.style.background="#4ade80";
  }

  logs.innerHTML=(s.logs||[]).slice().reverse().map(x=>{
   let cls=(x.status==="OK"||x.status==="TOTAL")?"ok":
           (x.status==="CACHED")?"cache":
           (x.status==="NO RESULTS")?"warn":"bad";
   let p=x.status==="TOTAL"?"TOTAL":"P"+x.page;
   return '<div class="log">'+
    '<span>#'+x.index+'</span>'+
    '<span class="q" title="'+esc(x.query||"")+'">'+esc(x.query||"")+'</span>'+
    '<span>'+p+'</span>'+
    '<span>'+x.urls+' URLs</span>'+
    '<span>'+x.new_urls+' new</span>'+
    '<span class="'+cls+'" title="'+esc(x.detail||"")+'">'+x.status+'</span>'+
   '</div>';
  }).join("") || '<div class="result small">No activity yet.</div>';

  const r=await (await fetch("/results")).json();
  results.innerHTML=r.length ? r.map(u=>
   '<div class="result"><a target="_blank" rel="noopener" href="'+encodeURI(u)+'">'+esc(u)+'</a></div>'
  ).join("") : '<div class="result small">No URLs yet.</div>';

 }catch(e){}
}
function esc(x){
 return String(x).replace(/&/g,"&amp;").replace(/</g,"&lt;")
  .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
loadProxies(); setInterval(update,1200); update();
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/status")
def status():
    dorks = load_dorks()
    actual = len(existing_urls())
    state["urls"] = actual
    engine_status, engine_detail = google_cse_status()

    return jsonify({
        **state,
        "total": len(dorks),
        "worker_alive":
            worker_thread is not None and worker_thread.is_alive(),
        "proxy_file": str(PROXY_FILE),
        "proxy_count": len(load_proxies()),
        "selected_proxy": state.get("selected_proxy", ""),
        "proxy_status": state.get("proxy_status", "NOT CONFIGURED"),
        "proxy_error": state.get("proxy_error", ""),
        "engine_status": engine_status,
        "engine_detail": engine_detail
    })


@app.route("/proxy/list")
def proxy_list():
    proxies = load_proxies()
    safe = []
    for p in proxies:
        try:
            from urllib.parse import urlsplit
            u = urlsplit(p)
            host = u.hostname or ""
            port = u.port
            safe.append({
                "value": p,
                "label": f"{u.scheme}://{host}:{port}" if port else f"{u.scheme}://{host}"
            })
        except Exception:
            safe.append({"value": p, "label": p})
    return jsonify(safe)


@app.route("/proxy/select", methods=["POST"])
def proxy_select():
    data = request.get_json(silent=True) or {}
    value = str(data.get("proxy", "")).strip()
    proxies = load_proxies()

    if value and value not in proxies:
        return jsonify({"ok": False, "error": "Proxy is not in proxies.txt"}), 400

    # Selecting a proxy is only allowed while paused/stopped, preventing
    # mid-request network changes.
    if state.get("status") == "RUNNING":
        return jsonify({
            "ok": False,
            "error": "Pause the worker before changing the proxy."
        }), 409

    state["selected_proxy"] = value
    state["proxy_status"] = "SELECTED" if value else "DIRECT"
    state["proxy_error"] = ""
    set_action(
        "PROXY SELECTED" if value else "DIRECT CONNECTION",
        "Press RESUME to use the selected network"
    )
    save_state()
    return jsonify({"ok": True})


@app.route("/proxy/test", methods=["POST"])
def proxy_test():
    proxy = state.get("selected_proxy", "")
    s = requests.Session()

    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})

    try:
        started = time.time()
        r = s.get(f"{SEARXNG_URL}/stats", headers={"User-Agent": UA}, timeout=12)
        elapsed = time.time() - started

        if r.ok:
            state["proxy_status"] = "CONNECTED"
            state["proxy_error"] = ""
            detail = f"HTTP {r.status_code} • {elapsed:.1f}s"
        else:
            state["proxy_status"] = "ERROR"
            state["proxy_error"] = f"HTTP {r.status_code}"
            detail = state["proxy_error"]

    except Exception as e:
        state["proxy_status"] = "ERROR"
        state["proxy_error"] = str(e)
        detail = str(e)

    save_state()
    return jsonify({
        "ok": state["proxy_status"] == "CONNECTED",
        "status": state["proxy_status"],
        "detail": detail
    })


@app.route("/logs")
def logs():
    return jsonify(state.get("logs", [])[-MAX_LOGS:])


@app.route("/results")
def results():
    if not RESULTS_FILE.exists():
        return jsonify([])

    lines = RESULTS_FILE.read_text(
        encoding="utf-8", errors="ignore"
    ).splitlines()

    urls = [
        x.strip() for x in lines
        if x.strip().startswith(("http://", "https://"))
    ]

    return jsonify(list(dict.fromkeys(reversed(urls)))[:100])


@app.route("/start", methods=["POST"])
def start():
    global worker_thread

    data = request.get_json(silent=True) or {}

    state["pages"] = max(
        1, min(MAX_PAGES, int(data.get("pages", state["pages"])))
    )
    state["page_gap"] = max(
        MIN_PAGE_GAP, float(data.get("page_gap", state["page_gap"]))
    )
    state["query_min"] = max(
        MIN_QUERY_DELAY, float(data.get("min_delay", state["query_min"]))
    )
    state["query_max"] = max(
        state["query_min"],
        float(data.get("max_delay", state["query_max"]))
    )

    stop_event.clear()
    pause_event.set()
    state["status"] = "RUNNING"
    state["last_error"] = ""
    if state.get("selected_proxy"):
        state["proxy_status"] = "SELECTED"
    else:
        state["proxy_status"] = "DIRECT"
    state["proxy_error"] = ""
    set_action("STARTING", "Checking Google CSE")
    save_state()

    if worker_thread is None or not worker_thread.is_alive():
        worker_thread = threading.Thread(
            target=worker,
            daemon=True
        )
        worker_thread.start()

    return jsonify({"ok": True})


@app.route("/pause", methods=["POST"])
def pause():
    pause_event.clear()
    state["status"] = "PAUSED"
    set_action("PAUSED", "Press RESUME to continue")
    save_state()
    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def stop():
    stop_event.set()
    pause_event.set()
    state["status"] = "STOPPED"
    set_action("STOPPED", "Progress is saved")
    save_state()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("Google Search Worker V8")
    print("Open http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, threaded=True)
