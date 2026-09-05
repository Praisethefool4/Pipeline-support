import os
import sys
import json
import time
from pathlib import Path
import requests

def fetch_master_url(repo_full_name):
    """Fetches the Master dashboard URL from GitHub Raw files to avoid git-pull lag."""
    urls = [
        f"https://raw.githubusercontent.com/{repo_full_name}/main/master_url.txt",
        f"https://raw.githubusercontent.com/{repo_full_name}/master/master_url.txt"
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                m_url = r.text.strip()
                if m_url.startswith("https://") or m_url.startswith("http://"):
                    return m_url
        except Exception:
            pass
    return None

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

def main():
    if len(sys.argv) < 3:
        print("Usage: python worker_agent.py <worker_id> <github_repo_full_name>")
        sys.exit(1)
        
    worker_id = sys.argv[1]
    repo_name = sys.argv[2]
    
    print(f"[Agent] Worker Agent started for Worker #{worker_id}")
    
    # 1. Wait for local Flask worker to be ready
    local_ready = False
    print("[Agent] Checking local worker status on http://127.0.0.1:5000/status...")
    for _ in range(30):
        try:
            r = requests.get("http://127.0.0.1:5000/status", timeout=2)
            if r.status_code == 200:
                print("[Agent] Local worker is online!")
                local_ready = True
                break
        except Exception:
            pass
        time.sleep(2)
        
    if not local_ready:
        print("[Agent] Warning: Local worker did not respond on port 5000. Proceeding anyway.")
        
    # 2. Wait for Master URL to be available in the repository
    master_url = None
    print("[Agent] Querying GitHub for master_url.txt...")
    for _ in range(60):
        master_url = fetch_master_url(repo_name)
        if master_url:
            print(f"[Agent] Successfully discovered Master URL: {master_url}")
            break
        print("[Agent] Master URL not ready yet in Git. Waiting...")
        time.sleep(5)
        
    if not master_url:
        print("[Agent] Error: Could not locate Master URL from GitHub. Exiting.")
        sys.exit(1)
        
    # 3. Periodically report stats & poll for commands
    total_dorks = get_total_dorks()
    print("[Agent] Entering bidirectional polling loop.")
    while True:
        state = load_worker_state()
        
        # Send update and poll for commands
        try:
            r = requests.post(
                f"{master_url}/poll",
                json={
                    "id": worker_id,
                    "state": state,
                    "total_dorks": total_dorks
                },
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                commands = data.get("commands", [])
                for cmd in commands:
                    print(f"[Agent] Executing command from Master: {cmd}")
                    try:
                        # Forward start/pause/stop request locally
                        res = requests.post(f"http://127.0.0.1:5000/{cmd}", json={}, timeout=5)
                        print(f"[Agent] Local {cmd} response: {res.status_code} - {res.text}")
                    except Exception as le:
                        print(f"[Agent] Failed to execute command locally: {le}")
            else:
                print(f"[Agent] Master returned unexpected status code: {r.status_code}")
        except Exception as e:
            print(f"[Agent] Master polling request failed: {e}")
            
        time.sleep(5)

if __name__ == "__main__":
    main()
