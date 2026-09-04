import os
import sys
import json
import time
import re
from pathlib import Path
import requests

def get_self_tunnel_url():
    """Extracts Cloudflare tunnel URL from the local log file."""
    log_file = Path("cloudflare_tunnel.log")
    if not log_file.exists():
        return None
        
    log_content = log_file.read_text(encoding="utf-8", errors="ignore")
    # Search for something like https://xxxxx.trycloudflare.com
    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log_content)
    if match:
        return match.group(0)
    return None

def fetch_master_url(repo_full_name, branch="main"):
    """Fetches the Master dashboard URL from GitHub Raw files to avoid git-pull lag."""
    url = f"https://raw.githubusercontent.com/{repo_full_name}/{branch}/master_url.txt"
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

def main():
    if len(sys.argv) < 3:
        print("Usage: python worker_agent.py <worker_id> <github_repo_full_name> [branch_name]")
        sys.exit(1)
        
    worker_id = sys.argv[1]
    repo_name = sys.argv[2]
    branch_name = sys.argv[3] if len(sys.argv) > 3 else "main"
    
    print(f"[Agent] Worker Agent started for Worker #{worker_id} on branch {branch_name}")
    
    # 1. Wait for Cloudflare Tunnel to be ready and get URL
    my_url = None
    for _ in range(30):
        my_url = get_self_tunnel_url()
        if my_url:
            print(f"[Agent] Located self tunnel URL: {my_url}")
            break
        print("[Agent] Waiting for local Cloudflare Tunnel log to generate URL...")
        time.sleep(2)
        
    if not my_url:
        print("[Agent] Error: Could not retrieve Cloudflare Tunnel URL. Exiting.")
        sys.exit(1)
        
    # 2. Wait for Master URL to be available in the repository
    master_url = None
    print(f"[Agent] Querying GitHub for master_url.txt from raw branch {branch_name}...")
    while not master_url:
        master_url = fetch_master_url(repo_name, branch_name)
        if master_url:
            print(f"[Agent] Successfully discovered Master URL: {master_url}")
            break
        print("[Agent] Master URL not ready yet in Git. Waiting...")
        time.sleep(5)
        
    # 3. Register with Master
    total_dorks = get_total_dorks()
    registered = False
    while not registered:
        try:
            r = requests.post(
                f"{master_url}/register",
                json={"id": worker_id, "url": my_url, "total_dorks": total_dorks},
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
        
    # 4. Periodically report stats & logs to Master
    print("[Agent] Entering status update loop.")
    while True:
        state = load_worker_state()
        if state:
            try:
                requests.post(
                    f"{master_url}/update",
                    json={"id": worker_id, "state": state},
                    timeout=5
                )
            except Exception as e:
                print(f"[Agent] Heartbeat update to Master failed: {e}")
        time.sleep(5)

if __name__ == "__main__":
    main()
