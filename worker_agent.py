import os
import sys
import json
import time
import re
from pathlib import Path
import requests

def fetch_master_urls(repo_full_name):
    """Fetches the list of Master dashboard URLs from GitHub Raw main branch."""
    url = f"https://raw.githubusercontent.com/{repo_full_name}/main/master_url.txt"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            urls = [line.strip() for line in r.text.splitlines() if line.strip().startswith("https://")]
            return urls
    except Exception as e:
        print(f"[Agent] Error fetching master URLs: {e}")
    return []

def get_total_dorks():
    """Reads my_dorks.txt to find how many dorks this worker has."""
    dork_file = Path("my_dorks.txt")
    if not dork_file.exists():
        return 0
    try:
        lines = [l.strip() for l in dork_file.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
        return len(lines)
    except Exception:
        return 0

def load_worker_state():
    """Loads current worker state from worker_state.json."""
    state_file = Path("worker_state.json")
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {}

def submit_new_urls(master_url, worker_id):
    """Reads new URLs harvested locally and pushes them to the Master Dashboard live."""
    results_file = Path("results.txt")
    if not results_file.exists():
        return
    
    pos_file = Path("last_read_pos.txt")
    last_pos = 0
    if pos_file.exists():
        try:
            last_pos = int(pos_file.read_text().strip())
        except Exception:
            pass
            
    current_size = results_file.stat().st_size
    if current_size > last_pos:
        try:
            with results_file.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(last_pos)
                new_lines = [line.strip() for line in f if line.strip().startswith(("http://", "https://"))]
                
            if new_lines:
                # Deduplicate batch
                new_lines = list(dict.fromkeys(new_lines))
                # Send in batches of 100 to avoid payload bloating
                batch_size = 100
                for i in range(0, len(new_lines), batch_size):
                    batch = new_lines[i:i+batch_size]
                    requests.post(
                        f"{master_url}/submit_urls",
                        json={"id": worker_id, "urls": batch},
                        timeout=8
                    )
            pos_file.write_text(str(current_size))
        except Exception as e:
            print(f"[Agent] URL submission to {master_url} failed: {e}")

def main():
    if len(sys.argv) < 3:
        print("Usage: python worker_agent.py <worker_id> <github_repo_full_name>")
        sys.exit(1)
        
    worker_id = sys.argv[1]
    repo_name = sys.argv[2]
    
    print(f"[Agent] Worker Agent started for Worker #{worker_id}")
    
    # 1. Wait for Master URLs to be available in the repository
    master_urls = []
    print("[Agent] Querying GitHub for master_url.txt...")
    while not master_urls:
        master_urls = fetch_master_urls(repo_name)
        if master_urls:
            print(f"[Agent] Successfully discovered Master URLs: {master_urls}")
            break
        print("[Agent] Master URLs not ready yet in Git. Waiting...")
        time.sleep(5)
        
    # 2. Register with the Master Dashboard (Try Cloudflare first, then Localtunnel)
    registered = False
    active_master_url = None
    total_dorks = get_total_dorks()
    
    while not registered:
        for m_url in master_urls:
            try:
                print(f"[Agent] Attempting registration at master: {m_url}")
                r = requests.post(
                    f"{m_url}/register",
                    json={"id": worker_id, "total_dorks": total_dorks},
                    timeout=10
                )
                if r.status_code == 200:
                    print(f"[Agent] Registered successfully with Master: {m_url}!")
                    registered = True
                    active_master_url = m_url
                    break
            except Exception as e:
                print(f"[Agent] Connection to master {m_url} failed: {e}")
        if not registered:
            print("[Agent] All registration attempts failed. Retrying in 5s...")
            time.sleep(5)
            # Re-fetch in case master restarted with new links
            new_urls = fetch_master_urls(repo_name)
            if new_urls:
                master_urls = new_urls
                
    # 3. Periodically stream stats & new URLs, and poll for commands from active Master
    print(f"[Agent] Entering active status and URL update loop against: {active_master_url}")
    while True:
        try:
            # First, submit any newly scraped URLs
            submit_new_urls(active_master_url, worker_id)
            
            # Next, load state and send update to master
            state = load_worker_state()
            if state:
                r = requests.post(
                    f"{active_master_url}/update",
                    json={"id": worker_id, "state": state},
                    timeout=5
                )
                if r.status_code == 200:
                    res_data = r.json()
                    cmd = res_data.get("command")
                    if cmd:
                        print(f"[Agent] Received control directive from Master: {cmd}")
                        try:
                            # Forward command to the local flask worker running on port 5000
                            # Map special actions
                            local_cmd = cmd
                            if cmd == "quarantine":
                                local_cmd = "pause"
                            elif cmd == "release":
                                local_cmd = "start"
                            requests.post(f"http://localhost:5000/{local_cmd}", json={}, timeout=5)
                        except Exception as ex:
                            print(f"[Agent] Failed to forward directive to local worker: {ex}")
        except Exception as e:
            print(f"[Agent] Heartbeat communication failed: {e}")
            
        time.sleep(5)

if __name__ == "__main__":
    main()
