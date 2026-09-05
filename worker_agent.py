import os
import sys
import json
import time
from pathlib import Path
import requests

def fetch_master_url(repo_full_name):
    """Fetches the Master dashboard URL from GitHub Raw files to avoid git-pull lag."""
    url = f"https://raw.githubusercontent.com/{repo_full_name}/main/master_url.txt"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            m_url = r.text.strip()
            if m_url.startswith("https://"):
                return m_url
    except Exception as e:
        print(f"[Agent] Error fetching master URL: {e}")
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

def execute_local_command(command):
    """Sends start, pause, or stop requests to the local worker's Flask interface."""
    try:
        url = f"http://127.0.0.1:5000/{command}"
        headers = {"Content-Type": "application/json"}
        # For start, pass default pages parameter to trigger run
        data = {"pages": 2} if command == "start" else {}
        r = requests.post(url, json=data, timeout=5)
        print(f"[Agent] Executed local command /{command}: {r.status_code} - {r.text.strip()}")
        return r.ok
    except Exception as e:
        print(f"[Agent] Failed to send /{command} to local worker: {e}")
        return False

def main():
    if len(sys.argv) < 3:
        print("Usage: python worker_agent.py <worker_id> <github_repo_full_name>")
        sys.exit(1)
        
    worker_id = sys.argv[1]
    repo_name = sys.argv[2]
    
    print(f"[Agent] Worker Agent started for Worker #{worker_id}")
    
    # 1. Wait for Master URL to be available in the repository
    master_url = None
    print("[Agent] Querying GitHub for master_url.txt...")
    while not master_url:
        master_url = fetch_master_url(repo_name)
        if master_url:
            print(f"[Agent] Successfully discovered Master URL: {master_url}")
            break
        print("[Agent] Master URL not ready yet in Git. Waiting...")
        time.sleep(5)
        
    # 2. Register with Master
    total_dorks = get_total_dorks()
    registered = False
    while not registered:
        try:
            r = requests.post(
                f"{master_url}/register",
                json={"id": worker_id, "total_dorks": total_dorks},
                timeout=10
            )
            if r.status_code == 200:
                print("[Agent] Registered successfully with Master dashboard!")
                registered = True
            else:
                print(f"[Agent] Failed to register: Status {r.status_code}. Retrying...")
        except Exception as e:
            print(f"[Agent] Connection to master failed: {e}. Retrying...")
        time.sleep(5)
        
    # 3. Periodically report stats & poll for commands from Master
    print("[Agent] Entering status update and polling loop.")
    while True:
        state = load_worker_state()
        if state:
            try:
                r = requests.post(
                    f"{master_url}/update",
                    json={"id": worker_id, "state": state},
                    timeout=5
                )
                if r.status_code == 200:
                    res_data = r.json()
                    command = res_data.get("command", "idle")
                    if command in ("start", "pause", "stop"):
                        print(f"[Agent] Received command '{command}' from Master. Executing...")
                        execute_local_command(command)
            except Exception as e:
                print(f"[Agent] Heartbeat update/poll to Master failed: {e}")
        time.sleep(5)

if __name__ == "__main__":
    main()
