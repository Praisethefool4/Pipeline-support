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
workers_data = {}  # key: worker_id, value: { state, last_seen, pending_command }

# Path to store final master results
RESULTS_FILE = Path("results.txt")

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Global Dorking Orchestrator (19-Worker Matrix)</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #0f172a;
            color: #f8fafc;
            font-family: system-ui, -apple-system, sans-serif;
            padding-bottom: 60px;
        }
        .navbar {
            background-color: #1e293b;
            border-bottom: 1px solid #334155;
        }
        .card {
            background-color: #1e293b;
            border: 1px solid #334155;
            color: #f8fafc;
            border-radius: 12px;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.4);
        }
        .status-badge {
            font-size: 0.8rem;
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 600;
        }
        .status-running { background-color: #16a34a; color: white; }
        .status-paused { background-color: #d97706; color: white; }
        .status-stopped { background-color: #dc2626; color: white; }
        .status-offline { background-color: #475569; color: white; }
        .status-completed { background-color: #2563eb; color: white; }
        .status-blocked { 
            background-color: #ef4444; 
            color: white;
            animation: pulse-red 1.5s infinite;
        }
        @keyframes pulse-red {
            0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
            70% { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); }
            100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }
        .progress {
            height: 8px;
            background-color: #334155;
            border-radius: 4px;
        }
        .progress-bar {
            background-color: #3b82f6;
        }
        .control-btn {
            border-radius: 8px;
            font-weight: 500;
        }
        .log-box {
            background-color: #090d16;
            color: #38bdf8;
            font-family: monospace;
            font-size: 0.85rem;
            height: 250px;
            overflow-y: auto;
            border-radius: 8px;
            padding: 12px;
            border: 1px solid #1e293b;
        }
        .text-error {
            color: #f87171 !important;
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark sticky-top mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1">🚀 Global Dorking Controller (19-Worker Setup)</span>
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
            <div class="col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted text-uppercase small font-weight-bold">Total Active Workers</h6>
                    <h2 id="stat-active-workers">0 / 19</h2>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted text-uppercase small font-weight-bold">Total Harvested URLs</h6>
                    <h2 id="stat-total-urls" class="text-success">0</h2>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted text-uppercase small font-weight-bold">Google Blocked Count</h6>
                    <h2 id="stat-blocked-count" class="text-danger">0</h2>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted text-uppercase small font-weight-bold">Master Tunnel Status</h6>
                    <h2 id="stat-master-status" class="text-info">ACTIVE</h2>
                </div>
            </div>
        </div>

        <!-- Workers Grid -->
        <h4 class="mb-3">Worker Matrix Nodes</h4>
        <div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 row-cols-xl-4 g-3" id="workers-grid">
            <!-- Dynamic Injection -->
        </div>

        <!-- Log Section -->
        <h4 class="mt-5 mb-3">Live Consolidated Activity Logs</h4>
        <div class="card p-3">
            <div class="log-box" id="activity-logs">
                [System Log] Dashboard initialized and listening for worker connections...
            </div>
        </div>
    </div>

    <script>
        async function fetchState() {
            try {
                const res = await fetch('/status_api');
                const data = await res.json();
                
                // Update Global Stats
                document.getElementById('stat-active-workers').innerText = `${data.active_count} / 19`;
                document.getElementById('stat-total-urls').innerText = data.total_master_urls;
                
                // Render Workers
                const grid = document.getElementById('workers-grid');
                grid.innerHTML = '';
                
                let logs = [];
                let blockedCount = 0;
                
                Object.keys(data.workers).sort((a,b) => parseInt(a) - parseInt(b)).forEach(id => {
                    const worker = data.workers[id];
                    const wState = worker.state || {};
                    const lastQuery = wState.last_query || 'None';
                    const harvested = wState.urls || 0;
                    const dorkIndex = wState.index || 0;
                    const totalDorks = worker.total_dorks || 1000;
                    const pct = totalDorks > 0 ? Math.round((dorkIndex / totalDorks) * 100) : 0;
                    const lastError = wState.last_error || '';
                    
                    let statusClass = 'status-offline';
                    let wStatus = worker.is_online ? (wState.status || 'PAUSED') : 'OFFLINE';
                    
                    // Detect Google block/429/suspended
                    const isBlocked = wStatus.includes('BLOCKED') || 
                                      wStatus.includes('SUSPENDED') || 
                                      lastError.toLowerCase().includes('429') || 
                                      lastError.toLowerCase().includes('suspended') || 
                                      lastError.toLowerCase().includes('captcha');
                                      
                    if (isBlocked && worker.is_online) {
                        wStatus = 'BLOCKED (429)';
                        statusClass = 'status-blocked';
                        blockedCount++;
                    } else if (wStatus === 'RUNNING') {
                        statusClass = 'status-running';
                    } else if (wStatus === 'AUTO-PAUSED' || wStatus === 'PAUSED') {
                        statusClass = 'status-paused';
                    } else if (wStatus === 'STOPPED') {
                        statusClass = 'status-stopped';
                    } else if (wStatus === 'FINISHED') {
                        statusClass = 'status-completed';
                    }
                    
                    const errorBlock = lastError ? `<div class="text-error small text-truncate mt-1">⚠️ ${lastError}</div>` : '';
                    const pendingCmd = worker.pending_command && worker.pending_command !== 'idle' ? 
                                       `<small class="text-info d-block mt-1">🕒 Sending: ${worker.pending_command.toUpperCase()}...</small>` : '';
                    
                    const cardHtml = `
                        <div class="col">
                            <div class="card p-3 ${statusClass === 'status-blocked' ? 'border-danger' : ''}">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="m-0">Worker #${id}</h5>
                                    <span class="status-badge ${statusClass}">${wStatus}</span>
                                </div>
                                <div class="mb-2">
                                    <small class="text-muted">Current Query:</small>
                                    <div class="text-truncate fw-bold" style="max-width: 100%; color: #38bdf8;">${lastQuery}</div>
                                    ${errorBlock}
                                    ${pendingCmd}
                                </div>
                                <div class="row mb-2">
                                    <div class="col-6">
                                        <small class="text-muted d-block">Harvested URLs</small>
                                        <span class="fw-bold text-success">${harvested}</span>
                                    </div>
                                    <div class="col-6 text-end">
                                        <small class="text-muted d-block">Progress</small>
                                        <span class="fw-bold">${dorkIndex} / ${totalDorks}</span>
                                    </div>
                                </div>
                                <div class="progress mb-3">
                                    <div class="progress-bar" role="progressbar" style="width: ${pct}%" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                                <div class="d-flex justify-content-end">
                                    <div>
                                        <button onclick="controlWorker(${id}, 'start')" class="btn btn-sm btn-success me-1" title="Start/Resume">▶</button>
                                        <button onclick="controlWorker(${id}, 'pause')" class="btn btn-sm btn-warning me-1" title="Pause">⏸</button>
                                        <button onclick="controlWorker(${id}, 'stop')" class="btn btn-sm btn-danger" title="Stop">⏹</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                    grid.innerHTML += cardHtml;
                    
                    // Aggregate logs
                    if (wState.logs && wState.logs.length > 0) {
                        wState.logs.slice(-3).forEach(l => {
                            logs.push(`[Worker #${id}] [${l.time}] Q:${l.query} P:${l.page} -> Found: ${l.urls} [${l.status}]`);
                        });
                    }
                });
                
                document.getElementById('stat-blocked-count').innerText = blockedCount;
                
                if (logs.length > 0) {
                    const logContainer = document.getElementById('activity-logs');
                    logContainer.innerHTML = logs.reverse().slice(0, 100).join('<br>');
                }
                
            } catch (err) {
                console.error("Error updating dashboard:", err);
            }
        }

        async function globalAction(action) {
            if (!confirm(`Are you sure you want to trigger "${action.toUpperCase()}" on all 19 workers?`)) return;
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

        setInterval(fetchState, 3000);
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
    total_dorks = data.get("total_dorks", 0)
    
    if not worker_id:
        return jsonify({"ok": False, "error": "Missing worker id"}), 400
        
    with workers_lock:
        workers_data[str(worker_id)] = {
            "total_dorks": total_dorks,
            "state": {},
            "last_seen": time.time(),
            "pending_command": "start"  # Auto-trigger start on worker registration!
        }
    print(f"[Master] Worker #{worker_id} successfully registered!")
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
            
            # Fetch and clear any pending command for this worker
            command = workers_data[str(worker_id)].get("pending_command", "idle")
            workers_data[str(worker_id)]["pending_command"] = "idle"
            
            return jsonify({"ok": True, "command": command})
            
    return jsonify({"ok": False, "error": "Worker not registered"}), 404

@app.route("/status_api")
def status_api():
    now = time.time()
    formatted_workers = {}
    active_count = 0
    
    with workers_lock:
        for w_id, w_info in workers_data.items():
            is_online = (now - w_info["last_seen"]) < 20  # Online if seen in last 20 seconds
            if is_online:
                active_count += 1
            formatted_workers[w_id] = {
                "total_dorks": w_info["total_dorks"],
                "state": w_info["state"],
                "is_online": is_online,
                "pending_command": w_info.get("pending_command", "idle")
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
        if str(w_id) in workers_data:
            workers_data[str(w_id)]["pending_command"] = action
            return jsonify({"ok": True})
            
    return jsonify({"ok": False, "error": "Worker not found"}), 404

@app.route("/global_control", methods=["POST"])
def global_control():
    action = request.args.get("action")
    if not action:
        return jsonify({"ok": False, "error": "Missing action"}), 400
        
    with workers_lock:
        for w_id in workers_data:
            workers_data[w_id]["pending_command"] = action
            
    return jsonify({"ok": True, "success_count": len(workers_data)})

def autoshutdown_check():
    """Background check: shutdown master cleanly if all registered workers are done or offline."""
    time.sleep(240)  # 4 minutes grace period for initial startup and registration
    print("[Master] Shutdown monitor is now active.")
    while True:
        try:
            with workers_lock:
                total = len(workers_data)
                if total > 0:
                    now = time.time()
                    online_count = sum(1 for w in workers_data.values() if (now - w["last_seen"]) < 45)
                    active_count = sum(
                        1 for w in workers_data.values() 
                        if w.get("state", {}).get("status") in ("RUNNING", "PAUSED", "AUTO-PAUSED")
                        and (now - w["last_seen"]) < 45
                    )
                    
                    # Exit condition: workers registered, but zero online/active workers remain
                    if online_count == 0 or active_count == 0:
                        print("[Master] Auto-Shutdown triggered. All workers have completed, paused, or gone offline.")
                        os._exit(0)
        except Exception as e:
            print(f"[Master Shutdown Monitor Error] {e}")
        time.sleep(15)

if __name__ == "__main__":
    # Start auto-shutdown monitor thread
    threading.Thread(target=autoshutdown_check, daemon=True).start()
    
    # Start server
    app.run(host="0.0.0.0", port=5000, threaded=True)
