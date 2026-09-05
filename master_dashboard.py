from flask import Flask, render_template_string, jsonify, request
import json
import threading
import time
import os
import subprocess
from pathlib import Path
import requests

app = Flask(__name__)

# Core state variables
workers_lock = threading.Lock()
workers_data = {}        # key: worker_id, value: { url, state, last_seen, total_dorks }
pending_commands = {}    # key: worker_id, value: command_string

# Live URL Feed state
live_urls_lock = threading.Lock()
live_urls_feed = []      # list of dicts: {"worker_id": id, "url": url, "time": time}

# Path to store final consolidated master results
RESULTS_FILE = Path("results.txt")

# Background thread state for GitHub Release auto-publisher
release_status_info = {
    "last_published_at": "Never",
    "status": "Initializing...",
    "total_published": 0
}

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🛰️ Dorking Core Matrix - Tactical Operations Control</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
        
        body {
            background-color: #050811;
            color: #f8fafc;
            font-family: 'Share Tech Mono', monospace;
            padding-bottom: 60px;
            overflow-x: hidden;
        }
        .navbar {
            background-color: rgba(15, 23, 42, 0.95);
            border-bottom: 2px solid #00f3ff;
            box-shadow: 0 0 15px rgba(0, 243, 255, 0.4);
        }
        .navbar-brand {
            font-size: 1.5rem;
            letter-spacing: 2px;
            color: #00f3ff !important;
            text-shadow: 0 0 10px rgba(0, 243, 255, 0.8);
        }
        .cyber-panel {
            background-color: #0a0f1d;
            border: 1px solid #1e293b;
            border-radius: 8px;
            box-shadow: inset 0 0 10px rgba(0,0,0,0.8);
            position: relative;
        }
        .cyber-header {
            font-size: 1.1rem;
            color: #00f3ff;
            text-transform: uppercase;
            letter-spacing: 1px;
            border-bottom: 1px solid #1e293b;
            padding-bottom: 8px;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .card {
            background-color: #0a0f1d;
            border: 1px solid #1e293b;
            color: #f8fafc;
            border-radius: 8px;
            transition: all 0.3s ease;
        }
        .card:hover {
            border-color: #00f3ff;
            box-shadow: 0 0 12px rgba(0, 243, 255, 0.3);
            transform: translateY(-1px);
        }
        .status-badge {
            font-size: 0.8rem;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: bold;
            letter-spacing: 1px;
        }
        .status-running { 
            background-color: rgba(0, 255, 102, 0.15); 
            color: #00ff66; 
            border: 1px solid #00ff66;
            text-shadow: 0 0 8px rgba(0,255,102,0.5);
            animation: pulse-green 1.5s infinite;
        }
        .status-paused { 
            background-color: rgba(245, 158, 11, 0.15); 
            color: #f59e0b; 
            border: 1px solid #f59e0b;
        }
        .status-quarantined { 
            background-color: rgba(255, 0, 85, 0.2); 
            color: #ff0055; 
            border: 2px solid #ff0055;
            text-shadow: 0 0 8px rgba(255,0,85,0.6);
            animation: pulse-red 1s infinite;
        }
        .status-offline { 
            background-color: rgba(71, 85, 105, 0.15); 
            color: #94a3b8; 
            border: 1px solid #475569;
        }
        .status-completed { 
            background-color: rgba(59, 130, 246, 0.15); 
            color: #3b82f6; 
            border: 1px solid #3b82f6;
        }
        .stat-val {
            font-size: 2.2rem;
            font-weight: bold;
            color: #ffffff;
            text-shadow: 0 0 10px rgba(255,255,255,0.2);
        }
        .stat-val-cyan { color: #00f3ff; text-shadow: 0 0 10px rgba(0,243,255,0.4); }
        .stat-val-green { color: #00ff66; text-shadow: 0 0 10px rgba(0,255,102,0.4); }
        .stat-val-red { color: #ff0055; text-shadow: 0 0 10px rgba(255,0,85,0.4); }
        
        .progress {
            height: 6px;
            background-color: #0d1527;
            border-radius: 3px;
            border: 1px solid #1e293b;
        }
        .progress-bar {
            background-color: #00f3ff;
            box-shadow: 0 0 8px #00f3ff;
        }
        .control-btn {
            border-radius: 4px;
            font-weight: bold;
            letter-spacing: 1px;
            text-transform: uppercase;
            transition: all 0.2s;
        }
        .btn-cyan {
            background-color: rgba(0, 243, 255, 0.1);
            color: #00f3ff;
            border: 1px solid #00f3ff;
        }
        .btn-cyan:hover {
            background-color: #00f3ff;
            color: #050811;
            box-shadow: 0 0 10px rgba(0, 243, 255, 0.5);
        }
        .log-box {
            background-color: #03050c;
            color: #00ff66;
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.85rem;
            height: 250px;
            overflow-y: auto;
            border-radius: 4px;
            padding: 12px;
            border: 1px solid #10b981;
            box-shadow: inset 0 0 15px rgba(0,255,102,0.1);
        }
        .url-box {
            background-color: #03050c;
            color: #00f3ff;
            font-family: 'Share Tech Mono', monospace;
            font-size: 0.82rem;
            height: 250px;
            overflow-y: auto;
            border-radius: 4px;
            padding: 12px;
            border: 1px solid #00f3ff;
            box-shadow: inset 0 0 15px rgba(0,243,255,0.1);
        }
        @keyframes pulse-green {
            0% { box-shadow: 0 0 0 0 rgba(0, 255, 102, 0.4); }
            70% { box-shadow: 0 0 0 8px rgba(0, 255, 102, 0); }
            100% { box-shadow: 0 0 0 0 rgba(0, 255, 102, 0); }
        }
        @keyframes pulse-red {
            0% { box-shadow: 0 0 0 0 rgba(255, 0, 85, 0.6); }
            70% { box-shadow: 0 0 0 10px rgba(255, 0, 85, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 0, 85, 0); }
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark sticky-top mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1">🛰️ DORKING MATRIX CORE CONTROL [V12]</span>
            <div class="d-flex">
                <button onclick="globalAction('start')" class="btn btn-sm btn-outline-success me-2 control-btn">▶ Broadcast Start</button>
                <button onclick="globalAction('pause')" class="btn btn-sm btn-outline-warning me-2 control-btn">⏸ Broadcast Pause</button>
                <button onclick="globalAction('stop')" class="btn btn-sm btn-outline-danger control-btn">⏹ Broadcast Stop</button>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Dashboard Live Status Summary -->
        <div class="row g-3 mb-4">
            <div class="col-md-3">
                <div class="cyber-panel p-3 text-center">
                    <div class="text-muted text-uppercase mb-1 small">Online Workers</div>
                    <div id="stat-active-workers" class="stat-val stat-val-cyan">0 / 19</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="cyber-panel p-3 text-center">
                    <div class="text-muted text-uppercase mb-1 small">URLs Harvested (Clean)</div>
                    <div id="stat-total-urls" class="stat-val stat-val-green">0</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="cyber-panel p-3 text-center">
                    <div class="text-muted text-uppercase mb-1 small">Quarantined Nodes (429 / Ban)</div>
                    <div id="stat-quarantined" class="stat-val stat-val-red">0</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="cyber-panel p-3 text-center">
                    <div class="text-muted text-uppercase mb-1 small">GitHub Release Asset</div>
                    <div id="stat-release-published" class="stat-val" style="font-size: 1.1rem; padding-top: 10px; color:#a5f3fc;">
                        Published: <span id="stat-release-count" class="fw-bold">0</span> times<br>
                        <span class="small text-muted" id="stat-release-time">Syncing...</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="row g-3 mb-4">
            <!-- Left Console: System Logs -->
            <div class="col-lg-6">
                <div class="cyber-panel p-3">
                    <div class="cyber-header">🛡️ Live Security & Orchestrator Logs</div>
                    <div class="log-box" id="activity-logs">
                        [System Log] Global controller active. Waiting for 19 dork workers to initialize...
                    </div>
                </div>
            </div>
            
            <!-- Right Console: Live URL Stream -->
            <div class="col-lg-6">
                <div class="cyber-panel p-3">
                    <div class="cyber-header">⚡ Live Unique URL Harvesting Stream</div>
                    <div class="url-box" id="url-stream">
                        [URL Stream] Silent... Waiting for workers to extract URLs.
                    </div>
                </div>
            </div>
        </div>

        <!-- Node Matrix -->
        <div class="cyber-panel p-3 mb-4">
            <div class="cyber-header">🌐 Scale-Out Node Matrix (19 Workers)</div>
            <div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 row-cols-xl-4 g-3" id="workers-grid">
                <!-- Grid injection of 19 workers -->
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
                document.getElementById('stat-quarantined').innerText = data.quarantined_count;
                document.getElementById('stat-release-count').innerText = data.release_info.total_published;
                document.getElementById('stat-release-time').innerText = `Last Sync: ${data.release_info.last_published_at}`;
                
                // Render Matrix Nodes
                const grid = document.getElementById('workers-grid');
                grid.innerHTML = '';
                
                let logs = [];
                
                // Create a guaranteed set of 19 worker cards
                for (let i = 1; i <= 19; i++) {
                    const idStr = i.toString();
                    const worker = data.workers[idStr] || { is_online: false, total_dorks: 0, state: {} };
                    const wState = worker.state || {};
                    const lastQuery = wState.last_query || 'None';
                    const harvested = wState.urls || 0;
                    const dorkIndex = wState.index || 0;
                    const totalDorks = worker.total_dorks || 0;
                    const pct = totalDorks > 0 ? Math.round((dorkIndex / totalDorks) * 100) : 0;
                    
                    let statusClass = 'status-offline';
                    let wStatus = 'OFFLINE';
                    let cardBorder = '';
                    let actionButtonHtml = '';
                    
                    if (worker.is_online) {
                        wStatus = wState.status || 'PAUSED';
                        if (wStatus === 'RUNNING') {
                            statusClass = 'status-running';
                        } else if (wStatus === 'PAUSED' || wStatus === 'AUTO-PAUSED') {
                            statusClass = 'status-paused';
                        } else if (wStatus === 'QUARANTINED') {
                            statusClass = 'status-quarantined';
                            cardBorder = 'border: 2px solid #ff0055;';
                        } else if (wStatus === 'FINISHED') {
                            statusClass = 'status-completed';
                        }
                    }
                    
                    if (wStatus === 'QUARANTINED') {
                        actionButtonHtml = `<button onclick="controlWorker(${i}, 'release')" class="btn btn-sm btn-outline-success w-100 fw-bold">⚠️ Release node</button>`;
                    } else if (worker.is_online) {
                        actionButtonHtml = `
                            <div class="d-flex gap-1 w-100">
                                <button onclick="controlWorker(${i}, 'start')" class="btn btn-sm btn-outline-success flex-fill">▶</button>
                                <button onclick="controlWorker(${i}, 'pause')" class="btn btn-sm btn-outline-warning flex-fill">⏸</button>
                                <button onclick="controlWorker(${i}, 'quarantine')" class="btn btn-sm btn-outline-danger flex-fill">⚠️ Quarantine</button>
                            </div>
                        `;
                    } else {
                        actionButtonHtml = `<button class="btn btn-sm btn-outline-secondary w-100" disabled>Inactive VM</button>`;
                    }
                    
                    const cardHtml = `
                        <div class="col">
                            <div class="card p-3" style="${cardBorder}">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5 class="m-0 text-white">Worker Node #${i}</h5>
                                    <span class="status-badge ${statusClass}">${wStatus}</span>
                                </div>
                                <div class="mb-2">
                                    <small class="text-muted d-block uppercase" style="font-size:0.75rem;">Active Target Dork</small>
                                    <div class="text-truncate fw-bold" style="max-width: 100%; color: #00f3ff; font-size:0.85rem;" title="${lastQuery}">${lastQuery}</div>
                                </div>
                                <div class="row mb-2">
                                    <div class="col-6">
                                        <small class="text-muted d-block" style="font-size:0.75rem;">URLs Harvested</small>
                                        <span class="fw-bold text-success" style="font-size:1.1rem;">${harvested}</span>
                                    </div>
                                    <div class="col-6 text-end">
                                        <small class="text-muted d-block" style="font-size:0.75rem;">Progress</small>
                                        <span class="fw-bold text-white">${dorkIndex} / ${totalDorks}</span>
                                    </div>
                                </div>
                                <div class="progress mb-3">
                                    <div class="progress-bar" role="progressbar" style="width: ${pct}%" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                                <div class="d-flex justify-content-between mt-1">
                                    ${actionButtonHtml}
                                </div>
                            </div>
                        </div>
                    `;
                    grid.innerHTML += cardHtml;
                    
                    // Collect recent logs for the log panel
                    if (wState.logs && wState.logs.length > 0) {
                        wState.logs.slice(-3).forEach(l => {
                            logs.push({
                                time: l.time,
                                text: `[Node #${i}] dork:${l.query} • Found: ${l.urls} [${l.status}]`
                            });
                        });
                    }
                }
                
                // Render Activity Logs
                if (logs.length > 0) {
                    const logContainer = document.getElementById('activity-logs');
                    // Sort logs chronologically and get last 50
                    logs.sort((a,b) => a.time.localeCompare(b.time));
                    logContainer.innerHTML = logs.reverse().slice(0, 50).map(l => `[${l.time}] ${l.text}`).join('<br>');
                }
                
                // Render Live URLs feed
                const urlContainer = document.getElementById('url-stream');
                if (data.live_urls && data.live_urls.length > 0) {
                    urlContainer.innerHTML = data.live_urls.map(u => `[${u.time}] [Worker #${u.worker_id}] -> <span style="color:#ffffff;">${u.url}</span>`).join('<br>');
                } else {
                    urlContainer.innerHTML = `[URL Stream] Listening...`;
                }
                
            } catch (err) {
                console.error("Dashboard fetching failure:", err);
            }
        }

        async function globalAction(action) {
            if (!confirm(`Are you sure you want to broadcast "${action.toUpperCase()}" to all 19 workers?`)) return;
            try {
                await fetch(`/global_control?action=${action}`, { method: 'POST' });
                alert(`Broadcasted ${action.toUpperCase()} command successfully!`);
                fetchState();
            } catch (err) {
                alert("Failed to deliver broadcast command.");
            }
        }

        async function controlWorker(id, action) {
            try {
                const res = await fetch(`/control_worker?id=${id}&action=${action}`, { method: 'POST' });
                const data = await res.json();
                if (data.ok) {
                    fetchState();
                } else {
                    alert(`Action failed: ${data.error}`);
                }
            } catch (err) {
                alert("Asynchronous command delivery failed.");
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
        return jsonify({"ok": False, "error": "Missing worker ID"}), 400
        
    with workers_lock:
        workers_data[str(worker_id)] = {
            "total_dorks": total_dorks,
            "state": {},
            "is_online": True,
            "last_seen": time.time()
        }
    print(f"[Master] Worker Node #{worker_id} successfully established connection.")
    return jsonify({"ok": True})

@app.route("/update", methods=["POST"])
def update_status():
    data = request.get_json(silent=True) or {}
    worker_id = data.get("id")
    worker_state = data.get("state", {})
    
    if not worker_id:
        return jsonify({"ok": False, "error": "Missing worker ID"}), 400
        
    with workers_lock:
        if str(worker_id) in workers_data:
            workers_data[str(worker_id)]["state"] = worker_state
            workers_data[str(worker_id)]["last_seen"] = time.time()
            workers_data[str(worker_id)]["is_online"] = True
            
            # Pull command queue for this worker
            cmd = pending_commands.pop(str(worker_id), None)
            return jsonify({"ok": True, "command": cmd})
            
    return jsonify({"ok": False, "error": "Worker not registered"}), 404

@app.route("/submit_urls", methods=["POST"])
def submit_urls():
    data = request.get_json(silent=True) or {}
    worker_id = data.get("id")
    urls = data.get("urls", [])
    
    if not worker_id or not urls:
        return jsonify({"ok": False}), 400
        
    # Append unique urls directly to master's central results.txt file
    with open("results.txt", "a", encoding="utf-8") as f:
        for u in urls:
            f.write(u + "\n")
            
    # Add to live memory feed for dashboard display
    t_str = time.strftime("%H:%M:%S")
    with live_urls_lock:
        for u in urls:
            live_urls_feed.append({"worker_id": worker_id, "url": u, "time": t_str})
        # Keep only the last 100 entries to prevent memory swelling
        del live_urls_feed[:-100]
        
    return jsonify({"ok": True})

@app.route("/status_api")
def status_api():
    now = time.time()
    formatted_workers = {}
    active_count = 0
    quarantined_count = 0
    
    with workers_lock:
        for w_id, w_info in workers_data.items():
            # Check online state (online if active ping in last 20 seconds)
            is_online = (now - w_info["last_seen"]) < 20
            w_info["is_online"] = is_online
            
            w_state = w_info["state"] or {}
            w_status = w_state.get("status", "OFFLINE")
            
            if is_online:
                active_count += 1
                if w_status == "QUARANTINED":
                    quarantined_count += 1
                    
            formatted_workers[w_id] = {
                "total_dorks": w_info["total_dorks"],
                "state": w_state,
                "is_online": is_online
            }
            
    # Count unique URLs in master central results file
    total_master_urls = 0
    if RESULTS_FILE.exists():
        try:
            with RESULTS_FILE.open("r", encoding="utf-8", errors="ignore") as f:
                total_master_urls = len(set(line.strip() for line in f if line.strip().startswith(("http://", "https://"))))
        except Exception:
            pass
            
    with live_urls_lock:
        copied_urls = list(live_urls_feed)
        
    return jsonify({
        "active_count": active_count,
        "quarantined_count": quarantined_count,
        "total_master_urls": total_master_urls,
        "workers": formatted_workers,
        "live_urls": list(reversed(copied_urls)),
        "release_info": release_status_info
    })

@app.route("/control_worker", methods=["POST"])
def control_worker():
    w_id = request.args.get("id")
    action = request.args.get("action")  # start, pause, stop, quarantine, release
    
    if not w_id or not action:
        return jsonify({"ok": False, "error": "Missing id or action"}), 400
        
    with workers_lock:
        if str(w_id) not in workers_data:
            return jsonify({"ok": False, "error": "Worker not registered yet"}), 404
            
    # Treat custom actions
    if action == "quarantine":
        # Forces a pause on the worker and sets its status as Quarantined
        action = "pause"
        with workers_lock:
            workers_data[str(w_id)]["state"]["status"] = "QUARANTINED"
            
    elif action == "release":
        # Release worker back to dorking
        action = "start"
        with workers_lock:
            if workers_data[str(w_id)]["state"].get("status") == "QUARANTINED":
                workers_data[str(w_id)]["state"]["status"] = "PAUSED"
                
    pending_commands[str(w_id)] = action
    return jsonify({"ok": True, "detail": f"Command {action} successfully buffered for worker #{w_id}"})

@app.route("/global_control", methods=["POST"])
def global_control():
    action = request.args.get("action")
    if not action:
        return jsonify({"ok": False, "error": "Missing action"}), 400
        
    with workers_lock:
        targets = list(workers_data.keys())
        
    for w_id in targets:
        pending_commands[str(w_id)] = action
        
    return jsonify({"ok": True, "queued_count": len(targets)})

def github_release_publisher():
    """Background publisher that updates results.txt directly to a GitHub Release asset periodically."""
    time.sleep(120)  # Wait 2 minutes for runners to start saving URLs
    print("[Publisher] Automatic GitHub Release syncing thread active.")
    last_size = 0
    
    while True:
        try:
            results_file = Path("results.txt")
            if results_file.exists():
                current_size = results_file.stat().st_size
                if current_size > last_size:
                    print(f"[Publisher] Offset detected. Updating GitHub Release asset...")
                    release_status_info["status"] = "Uploading to GitHub..."
                    
                    # Create release tag 'live-results' if it doesn't exist, and clobber results.txt asset
                    cmd = "gh release create live-results results.txt --title 'Live Harvested Results' --notes 'Auto-consolidated results feed from Master Operations Control' --clobber || gh release upload live-results results.txt --clobber"
                    
                    result = subprocess.run(
                        cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        env=os.environ
                    )
                    
                    if result.returncode == 0:
                        last_size = current_size
                        release_status_info["total_published"] += 1
                        release_status_info["last_published_at"] = time.strftime("%H:%M:%S")
                        release_status_info["status"] = "Synced Successfully"
                        print("[Publisher] Successfully updated GitHub Release asset live!")
                    else:
                        release_status_info["status"] = f"GitHub CLI Error (Code {result.returncode})"
                        print(f"[Publisher] Error uploading asset: {result.stderr}")
        except Exception as e:
            release_status_info["status"] = "Internal Thread Error"
            print(f"[Publisher] Critical thread failure: {e}")
            
        time.sleep(180)  # Push delta releases every 3 minutes

def autoshutdown_check():
    """Shuts down the master controller cleanly once all workers are done or offline."""
    time.sleep(300)  # 5 minutes startup grace period
    print("[Master] Shutdown safety monitor initialized.")
    while True:
        try:
            with workers_lock:
                total = len(workers_data)
                if total > 0:
                    now = time.time()
                    online_count = sum(1 for w in workers_data.values() if (now - w["last_seen"]) < 60)
                    active_count = sum(
                        1 for w in workers_data.values()
                        if w.get("state", {}).get("status") in ("RUNNING", "PAUSED", "AUTO-PAUSED", "QUARANTINED")
                        and (now - w["last_seen"]) < 60
                    )
                    
                    if online_count == 0 or active_count == 0:
                        print("[Master] Zero online or active dorkers left. Triggering clean shut down...")
                        os._exit(0)
        except Exception as e:
            print(f"[Shutdown Safety Monitor Error] {e}")
        time.sleep(20)

if __name__ == "__main__":
    # Launch GitHub Release publisher background thread
    threading.Thread(target=github_release_publisher, daemon=True).start()
    
    # Launch Auto-shutdown safety monitor thread
    threading.Thread(target=autoshutdown_check, daemon=True).start()
    
    # Bind to port 5000 and run Flask
    app.run(host="0.0.0.0", port=5000, threaded=True)
