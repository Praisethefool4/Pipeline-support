from flask import Flask, render_template_string, jsonify, request
import json
import threading
import time
import os
from pathlib import Path
import requests

app = Flask(__name__)

# State storage for all registered workers
workers_lock = threading.Lock()
workers_data = {}  # key: worker_id, value: { url, state, last_seen }

# Path to store final master results
RESULTS_FILE = Path("results.txt")

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Global 20-Worker Dork Panel</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #0b0f19;
            color: #f1f5f9;
            font-family: system-ui, -apple-system, sans-serif;
            padding-bottom: 60px;
        }
        .navbar {
            background-color: #111827;
            border-bottom: 1px solid #1f2937;
        }
        .card {
            background-color: #111827;
            border: 1px solid #1f2937;
            color: #f1f5f9;
            border-radius: 12px;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        }
        .status-badge {
            font-size: 0.75rem;
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 700;
            text-transform: uppercase;
        }
        .status-running { background-color: #059669; color: #ecfdf5; }
        .status-paused { background-color: #d97706; color: #fffbeb; }
        .status-stopped { background-color: #dc2626; color: #fef2f2; }
        .status-offline { background-color: #4b5563; color: #f3f4f6; }
        .status-completed { background-color: #2563eb; color: #eff6ff; }
        .status-blocked { background-color: #e11d48; color: #fff1f2; animation: pulse-red 1.5s infinite; }
        
        @keyframes pulse-red {
            0% { transform: scale(1); }
            50% { transform: scale(1.03); background-color: #be123c; }
            100% { transform: scale(1); }
        }

        .progress {
            height: 10px;
            background-color: #374151;
            border-radius: 6px;
        }
        .progress-bar {
            background-color: #3b82f6;
            border-radius: 6px;
        }
        .control-btn {
            border-radius: 8px;
            font-weight: 600;
        }
        .log-box {
            background-color: #030712;
            color: #38bdf8;
            font-family: monospace;
            font-size: 0.8rem;
            height: 300px;
            overflow-y: auto;
            border-radius: 8px;
            padding: 16px;
            border: 1px solid #1f2937;
        }
        .worker-error-box {
            font-size: 0.75rem;
            background-color: rgba(225, 29, 72, 0.1);
            color: #f43f5e;
            border: 1px solid rgba(225, 29, 72, 0.2);
            border-radius: 6px;
            padding: 8px;
            margin-top: 8px;
            word-break: break-all;
        }
        .worker-ok-box {
            font-size: 0.75rem;
            background-color: rgba(16, 185, 129, 0.1);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.2);
            border-radius: 6px;
            padding: 8px;
            margin-top: 8px;
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark sticky-top mb-4">
        <div class="container-fluid px-4">
            <span class="navbar-brand mb-0 h1">🚀 Global Dorking Master Panel (20 Workers)</span>
            <div class="d-flex">
                <button onclick="globalAction('start')" class="btn btn-success me-2 control-btn">▶ Start All</button>
                <button onclick="globalAction('pause')" class="btn btn-warning me-2 control-btn">⏸ Pause All</button>
                <button onclick="globalAction('stop')" class="btn btn-danger control-btn">⏹ Stop All</button>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Stats Row -->
        <div class="row g-3 mb-4">
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted small">Active Workers</h6>
                    <h2 id="stat-active-workers">0 / 20</h2>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted small">Blocked Workers (429)</h6>
                    <h2 id="stat-blocked-workers" class="text-danger">0</h2>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted small">Total Harvested URLs</h6>
                    <h2 id="stat-total-urls" class="text-success">0</h2>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted small">Master Engine Health</h6>
                    <h2 id="stat-health" class="text-info">HEALTHY</h2>
                </div>
            </div>
        </div>

        <!-- Workers Grid -->
        <h4 class="mb-3">Worker Progress & Google CSE Status</h4>
        <div class="row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-lg-4 g-3" id="workers-grid">
            <!-- Dynamic Insertion -->
        </div>

        <!-- Log Section -->
        <h4 class="mt-5 mb-3">Consolidated Logs & Error Stream</h4>
        <div class="card p-3">
            <div class="log-box" id="activity-logs">
                [System Log] Dashboard initialized. Waiting for 20 workers to register and stream statistics...
            </div>
        </div>
    </div>

    <script>
        async function fetchState() {
            try {
                const res = await fetch('/status_api');
                const data = await res.json();
                
                let activeCount = data.active_count;
                let blockedCount = 0;
                
                // Update Global Stats
                document.getElementById('stat-total-urls').innerText = data.total_master_urls;
                
                // Render Workers
                const grid = document.getElementById('workers-grid');
                grid.innerHTML = '';
                
                let logs = [];
                
                // Sort keys numerically up to 20
                Object.keys(data.workers).sort((a,b) => parseInt(a) - parseInt(b)).forEach(id => {
                    const worker = data.workers[id];
                    const wState = worker.state || {};
                    const lastQuery = wState.last_query || 'None';
                    const totalDorks = worker.total_dorks || 0;
                    const dorkIndex = wState.index || 0;
                    const harvested = wState.urls || 0;
                    const sessionNew = wState.session_new_urls || 0;
                    const lastError = wState.last_error || '';
                    const actionText = wState.action || 'IDLE';
                    const pct = totalDorks > 0 ? Math.round((dorkIndex / totalDorks) * 100) : 0;
                    
                    // Determine if worker is currently blocked by Google CSE 429
                    let isBlocked = false;
                    const errLower = lastError.toLowerCase();
                    const actLower = actionText.toLowerCase();
                    if (wState.status === 'AUTO-PAUSED' || 
                        errLower.includes('429') || errLower.includes('blocked') || errLower.includes('suspended') || errLower.includes('captcha') ||
                        actLower.includes('suspended') || actLower.includes('captcha') || actLower.includes('status unknown')) {
                        isBlocked = true;
                        blockedCount++;
                    }

                    let statusClass = 'status-offline';
                    let wStatus = worker.is_online ? (wState.status || 'PAUSED') : 'OFFLINE';
                    
                    if (!worker.is_online) {
                        wStatus = 'OFFLINE';
                    } else if (isBlocked) {
                        wStatus = 'BLOCKED (429)';
                        statusClass = 'status-blocked';
                    } else if (wStatus === 'RUNNING') {
                        statusClass = 'status-running';
                    } else if (wStatus === 'PAUSED' || wStatus === 'AUTO-PAUSED') {
                        statusClass = 'status-paused';
                    } else if (wStatus === 'STOPPED') {
                        statusClass = 'status-stopped';
                    } else if (wStatus === 'FINISHED') {
                        statusClass = 'status-completed';
                    }

                    // Block description formatting
                    let blockMsgHtml = `<div class="worker-ok-box">✅ SearXNG Google CSE Healthy</div>`;
                    if (isBlocked) {
                        blockMsgHtml = `<div class="worker-error-box">⚠️ <strong>Rate Limited / Blocked:</strong><br>${lastError || actionText}</div>`;
                    } else if (wStatus === 'OFFLINE') {
                        blockMsgHtml = `<div class="text-muted small text-center p-2 border border-secondary rounded" style="background-color: rgba(255,255,255,0.03)">💤 Worker Offline</div>`;
                    }

                    const cardHtml = `
                        <div class="col">
                            <div class="card p-3">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="m-0">Worker #${id}</h5>
                                    <span class="status-badge ${statusClass}">${wStatus}</span>
                                </div>
                                
                                <div class="mb-2">
                                    <small class="text-muted small">Current Query:</small>
                                    <div class="text-truncate fw-bold" style="max-width: 100%; color: #38bdf8;" title="${lastQuery}">${lastQuery}</div>
                                </div>

                                <div class="row mb-2">
                                    <div class="col-6">
                                        <small class="text-muted small d-block">Harvested URLs</small>
                                        <span class="fw-bold text-success">${harvested} <small class="text-muted" style="font-size: 0.7rem;">(+${sessionNew})</small></span>
                                    </div>
                                    <div class="col-6 text-end">
                                        <small class="text-muted small d-block">Dork Progress</small>
                                        <span class="fw-bold" style="font-size: 0.9rem;">${dorkIndex} / ${totalDorks}</span>
                                    </div>
                                </div>

                                <div class="progress mb-2">
                                    <div class="progress-bar" role="progressbar" style="width: ${pct}%" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                                
                                ${blockMsgHtml}

                                <div class="d-flex justify-content-between align-items-center mt-3">
                                    <a href="${worker.url}" target="_blank" class="btn btn-sm btn-outline-secondary" style="font-size: 0.75rem;">Open UI</a>
                                    <div>
                                        <button onclick="controlWorker(${id}, 'start')" class="btn btn-xs btn-success py-1 px-2" style="font-size: 0.75rem;">▶</button>
                                        <button onclick="controlWorker(${id}, 'pause')" class="btn btn-xs btn-warning py-1 px-2" style="font-size: 0.75rem;">⏸</button>
                                        <button onclick="controlWorker(${id}, 'stop')" class="btn btn-xs btn-danger py-1 px-2" style="font-size: 0.75rem;">⏹</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                    grid.innerHTML += cardHtml;
                    
                    // Aggregate logs
                    if (wState.logs && wState.logs.length > 0) {
                        wState.logs.slice(-2).forEach(l => {
                            logs.push(`[Worker #${id}] [${l.time}] Q:${l.query} P:${l.page} -> Found: ${l.urls} (${l.status}) ${l.status.includes('SUSPENDED') ? '⚠️ BLOCKED' : ''}`);
                        });
                    }
                });
                
                document.getElementById('stat-active-workers').innerText = `${activeCount} / 20`;
                document.getElementById('stat-blocked-workers').innerText = blockedCount;
                if (blockedCount > 0) {
                    document.getElementById('stat-health').innerText = "BLOCKED SITES ACTIVE";
                    document.getElementById('stat-health').className = "text-danger";
                } else {
                    document.getElementById('stat-health').innerText = "ALL OK";
                    document.getElementById('stat-health').className = "text-info";
                }

                if (logs.length > 0) {
                    const logContainer = document.getElementById('activity-logs');
                    logContainer.innerHTML = logs.reverse().slice(0, 100).join('<br>');
                }
                
            } catch (err) {
                console.error("Error updating dashboard:", err);
            }
        }

        async function globalAction(action) {
            if (!confirm(`Are you sure you want to trigger "${action.toUpperCase()}" on all 20 workers?`)) return;
            try {
                await fetch(`/global_control?action=${action}`, { method: 'POST' });
                alert(`Broadcasted ${action.toUpperCase()} command to all active workers!`);
                fetchState();
            } catch (err) {
                alert("Failed to send global command.");
            }
        }

        async function controlWorker(id, action) {
            try {
                const res = await fetch(`/control_worker?id=${id}&action=${action}`, { method: 'POST' });
                const data = await res.json();
                if (data.ok) {
                    fetchState();
                } else {
                    alert(`Error: ${data.error}`);
                }
            } catch (err) {
                alert("Failed to communicate with worker.");
            }
        }

        setInterval(fetchState, 4000);
        window.onload = fetchState;
    </script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_DASHBOARD)

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    worker_id = data.get("id")
    worker_url = data.get("url")
    total_dorks = data.get("total_dorks", 0)
    
    if not worker_id or not worker_url:
        return jsonify({"ok": False, "error": "Missing worker id or url"}), 400
        
    with workers_lock:
        workers_data[str(worker_id)] = {
            "url": worker_url,
            "total_dorks": total_dorks,
            "state": {},
            "last_seen": time.time()
        }
    print(f"[Master] Worker #{worker_id} successfully registered at {worker_url}")
    return jsonify({"ok": True})

@app.route("/update", methods=["POST"])
def update_status():
    data = request.get_json(silent=True) or {}
    worker_id = data.get("id")
    worker_state = data.get("state", {})
    
    if not worker_id:
        return jsonify({"ok": False, "error": "Missing worker id"}), 400
        
    with workers_lock:
        if str(worker_id) in workers_data:
            workers_data[str(worker_id)]["state"] = worker_state
            workers_data[str(worker_id)]["last_seen"] = time.time()
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Worker not registered"}), 404

@app.route("/status_api")
def status_api():
    now = time.time()
    formatted_workers = {}
    active_count = 0
    
    with workers_lock:
        for w_id, w_info in workers_data.items():
            is_online = (now - w_info["last_seen"]) < 25  # Online if seen in last 25 seconds
            if is_online:
                active_count += 1
            formatted_workers[w_id] = {
                "url": w_info["url"],
                "total_dorks": w_info["total_dorks"],
                "state": w_info["state"],
                "is_online": is_online
            }
            
    # Count unique URLs harvested in global results file if it exists
    total_master_urls = 0
    if RESULTS_FILE.exists():
        try:
            with RESULTS_FILE.open("r", encoding="utf-8", errors="ignore") as f:
                total_master_urls = len(set(line.strip() for line in f if line.strip().startswith(("http://", "https://"))))
        except Exception:
            pass
            
    return jsonify({
        "active_count": active_count,
        "total_master_urls": total_master_urls,
        "workers": formatted_workers
    })

@app.route("/control_worker", methods=["POST"])
def control_worker():
    w_id = request.args.get("id")
    action = request.args.get("action")  # start, pause, stop
    
    if not w_id or not action:
        return jsonify({"ok": False, "error": "Missing id or action"}), 400
        
    with workers_lock:
        worker = workers_data.get(str(w_id))
        if not worker:
            return jsonify({"ok": False, "error": "Worker not found"}), 404
        worker_url = worker["url"]
        
    try:
        res = requests.post(f"{worker_url}/{action}", json={}, timeout=10)
        return jsonify({"ok": res.ok, "detail": res.json() if res.ok else "HTTP Error"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to forward command: {str(e)}"}), 500

@app.route("/global_control", methods=["POST"])
def global_control():
    action = request.args.get("action")
    if not action:
        return jsonify({"ok": False, "error": "Missing action"}), 400
        
    success_count = 0
    with workers_lock:
        targets = list(workers_data.items())
        
    for w_id, w_info in targets:
        try:
            requests.post(f"{w_info['url']}/{action}", json={}, timeout=5)
            success_count += 1
        except Exception:
            pass
            
    return jsonify({"ok": True, "success_count": success_count})

def autoshutdown_check():
    """Background check: shutdown master cleanly if all registered workers are done or offline."""
    time.sleep(300)  # 5 minutes grace period for initial startup and registration
    print("[Master] Shutdown monitor is now active.")
    while True:
        try:
            with workers_lock:
                total = len(workers_data)
                if total > 0:
                    now = time.time()
                    online_count = sum(1 for w in workers_data.values() if (now - w["last_seen"]) < 60)
                    active_count = sum(
                        1 for w in workers_data.values() 
                        if w.get("state", {}).get("status") in ("RUNNING", "PAUSED", "AUTO-PAUSED")
                        and (now - w["last_seen"]) < 60
                    )
                    
                    if online_count == 0 or active_count == 0:
                        print("[Master] Auto-Shutdown triggered. All workers have completed, paused, or gone offline.")
                        os._exit(0)
        except Exception as e:
            print(f"[Master Shutdown Monitor Error] {e}")
        time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=autoshutdown_check, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
