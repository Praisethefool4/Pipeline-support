from flask import Flask, render_template_string, jsonify, request
import json
import threading
import time
import os
from pathlib import Path

app = Flask(__name__)

# State storage for all registered workers
workers_lock = threading.Lock()
workers_data = {}        # key: worker_id, value: { url, state, last_seen, total_dorks }
pending_commands = {}    # key: worker_id, value: list of commands

# Path to store final master results
RESULTS_FILE = Path("results.txt")

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Global Dorking Orchestrator</title>
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
        .status-blocked { background-color: #dc2626; color: white; animation: pulse 1.5s infinite; }
        .status-stopped { background-color: #475569; color: white; }
        .status-offline { background-color: #334155; color: #94a3b8; }
        .status-completed { background-color: #2563eb; color: white; }
        
        @keyframes pulse {
            0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.7); }
            70% { transform: scale(1.03); box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); }
            100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); }
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
    </style>
</head>
<body>
    <nav class="navbar navbar-dark sticky-top mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1">🚀 Global Dorking Controller</span>
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
                    <h6 class="text-muted">Active Workers</h6>
                    <h2 id="stat-active-workers">0 / 19</h2>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted">Total Harvested URLs</h6>
                    <h2 id="stat-total-urls" class="text-success">0</h2>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted">Google Blocked Count</h6>
                    <h2 id="stat-blocked-count" class="text-danger">0</h2>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card p-3 text-center">
                    <h6 class="text-muted">Master Status</h6>
                    <h2 id="stat-master-status" class="text-info">ACTIVE</h2>
                </div>
            </div>
        </div>

        <!-- Workers Grid -->
        <h4 class="mb-3">Worker Matrix Nodes</h4>
        <div class="row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-lg-4 g-3" id="workers-grid">
            <!-- Dynamic Injection -->
        </div>

        <!-- Log Section -->
        <h4 class="mt-5 mb-3">Live Consolidated Activity Logs</h4>
        <div class="card p-3">
            <div class="log-box" id="activity-logs">
                [System Log] Dashboard initialized. Waiting for 19 workers to register and stream statistics...
            </div>
        </div>
    </div>

    <script>
        let lastLogs = [];

        async function fetchState() {
            try {
                const res = await fetch('/status_api');
                const data = await res.json();
                
                // Update Global Stats
                document.getElementById('stat-active-workers').innerText = `${data.active_count} / 19`;
                document.getElementById('stat-total-urls').innerText = data.total_master_urls;
                document.getElementById('stat-blocked-count').innerText = data.blocked_count;
                
                // Render Workers
                const grid = document.getElementById('workers-grid');
                grid.innerHTML = '';
                
                let incomingLogs = [];
                
                // Ensure all 19 indices exist
                for (let i = 1; i <= 19; i++) {
                    const idStr = i.toString();
                    const worker = data.workers[idStr] || { is_online: false, total_dorks: 0, state: {} };
                    const wState = worker.state || {};
                    const lastQuery = wState.last_query || 'None';
                    const harvested = wState.urls || 0;
                    const dorkIndex = wState.index || 0;
                    const totalDorks = worker.total_dorks || 0;
                    const pct = totalDorks > 0 ? Math.min(100, Math.round((dorkIndex / totalDorks) * 100)) : 0;
                    
                    let statusClass = 'status-offline';
                    let wStatus = 'OFFLINE';
                    
                    if (worker.is_online) {
                        wStatus = wState.status || 'PAUSED';
                        if (wStatus === 'RUNNING') statusClass = 'status-running';
                        else if (wStatus.includes('PAUSE')) statusClass = 'status-paused';
                        else if (wStatus.includes('SUSPENDED') || wStatus.includes('BLOCKED') || wStatus.includes('429')) {
                            statusClass = 'status-blocked';
                        }
                        else if (wStatus === 'STOPPED') statusClass = 'status-stopped';
                        else if (wStatus === 'FINISHED') statusClass = 'status-completed';
                    }
                    
                    const cardHtml = `
                        <div class="col">
                            <div class="card p-3 ${statusClass === 'status-blocked' ? 'border-danger' : ''}">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="m-0">Worker #${i}</h5>
                                    <span class="status-badge ${statusClass}">${wStatus}</span>
                                </div>
                                <div class="mb-2">
                                    <small class="text-muted">Current Query:</small>
                                    <div class="text-truncate fw-bold" style="max-width: 100%; color: #38bdf8;" title="${lastQuery}">${lastQuery}</div>
                                </div>
                                <div class="row mb-2">
                                    <div class="col-6">
                                        <small class="text-muted d-block">Harvested URLs</small>
                                        <span class="fw-bold text-success">${harvested}</span>
                                    </div>
                                    <div class="col-6 text-end">
                                        <small class="text-muted d-block">Progress</small>
                                        <span class="fw-bold text-info">${dorkIndex} / ${totalDorks}</span>
                                    </div>
                                </div>
                                <div class="progress mb-3">
                                    <div class="progress-bar" role="progressbar" style="width: ${pct}%" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                                <div class="d-flex justify-content-end">
                                    <div class="btn-group w-100">
                                        <button onclick="controlWorker(${i}, 'start')" class="btn btn-sm btn-success">▶ Start</button>
                                        <button onclick="controlWorker(${i}, 'pause')" class="btn btn-sm btn-warning">⏸ Pause</button>
                                        <button onclick="controlWorker(${i}, 'stop')" class="btn btn-sm btn-danger">⏹ Stop</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                    grid.innerHTML += cardHtml;
                    
                    // Pull logs
                    if (wState.logs && wState.logs.length > 0) {
                        wState.logs.slice(-3).forEach(l => {
                            incomingLogs.push({
                                time: l.time || '00:00:00',
                                msg: `[Worker #${i}] Q:${l.query || 'N/A'} (Page ${l.page || 1}) -> Found: ${l.urls || 0} [${l.status || 'OK'}]`
                            });
                        });
                    }
                }
                
                // Show aggregated logs
                if (incomingLogs.length > 0) {
                    // Sort logs by time
                    incomingLogs.sort((a, b) => b.time.localeCompare(a.time));
                    const logContainer = document.getElementById('activity-logs');
                    logContainer.innerHTML = incomingLogs.slice(0, 100).map(l => `[${l.time}] ${l.msg}`).join('<br>');
                }
                
            } catch (err) {
                console.error("Error updating dashboard:", err);
            }
        }

        async function globalAction(action) {
            try {
                const res = await fetch(`/global_control?action=${action}`, { method: 'POST' });
                const data = await res.json();
                console.log(`Global ${action}:`, data);
                fetchState();
            } catch (err) {
                console.error("Failed to send global command:", err);
            }
        }

        async function controlWorker(id, action) {
            try {
                const res = await fetch(`/control_worker?id=${id}&action=${action}`, { method: 'POST' });
                const data = await res.json();
                console.log(`Worker #${id} command:`, data);
                fetchState();
            } catch (err) {
                console.error("Failed to send worker command:", err);
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

@app.route("/poll", methods=["POST"])
def poll():
    data = request.get_json(silent=True) or {}
    worker_id = str(data.get("id"))
    worker_state = data.get("state", {})
    total_dorks = data.get("total_dorks", 0)
    
    if not worker_id:
        return jsonify({"ok": False, "error": "Missing worker id"}), 400
        
    with workers_lock:
        workers_data[worker_id] = {
            "total_dorks": total_dorks,
            "state": worker_state,
            "last_seen": time.time()
        }
        
        # Pop pending commands for this worker
        commands = pending_commands.get(worker_id, [])
        pending_commands[worker_id] = []
        
    return jsonify({"ok": True, "commands": commands})

@app.route("/status_api")
def status_api():
    now = time.time()
    formatted_workers = {}
    active_count = 0
    blocked_count = 0
    
    with workers_lock:
        for w_id, w_info in workers_data.items():
            is_online = (now - w_info["last_seen"]) < 20  # Online if seen in last 20s
            if is_online:
                active_count += 1
                status = w_info.get("state", {}).get("status", "PAUSED")
                if "BLOCKED" in status or "SUSPENDED" in status or "429" in status:
                    blocked_count += 1
                    
            formatted_workers[w_id] = {
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
        "blocked_count": blocked_count,
        "total_master_urls": total_master_urls,
        "workers": formatted_workers
    })

@app.route("/control_worker", methods=["POST"])
def control_worker():
    w_id = str(request.args.get("id"))
    action = request.args.get("action")  # start, pause, stop
    
    if not w_id or not action:
        return jsonify({"ok": False, "error": "Missing id or action"}), 400
        
    with workers_lock:
        pending_commands.setdefault(w_id, []).append(action)
        
    return jsonify({"ok": True, "message": f"Command '{action}' queued for Worker #{w_id}"})

@app.route("/global_control", methods=["POST"])
def global_control():
    action = request.args.get("action")
    if not action:
        return jsonify({"ok": False, "error": "Missing action"}), 400
        
    with workers_lock:
        # Send command to all 19 slots
        for i in range(1, 20):
            w_id = str(i)
            pending_commands.setdefault(w_id, []).append(action)
            
    return jsonify({"ok": True, "message": f"Global command '{action}' broadcasted to all workers."})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
