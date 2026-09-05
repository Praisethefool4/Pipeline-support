import os
import sys
import json
import time
import re
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

def stream_new_urls(master_url, worker_id, offset_file):
    """Streams new unique URLs to the master using file position offset tracking."""
    results_file = Path("results.txt")
    if not results_file.exists():
        return
        
    pos = 0
    if offset_file.exists():
        try:
            pos = int(offset_file.read_text().strip())
        except Exception:
            pass
            
    file_size = results_file.stat().st_size
    if file_size < pos:
        pos = 0  # File was wiped or truncated, restart
        
    if file_size > pos:
        try:
            with results_file.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(pos)
                new_urls = [line.strip() for line in f if line.strip().startswith(("http://", "https://"))]
                new_pos = f.tell()
                
            if new_urls:
                print(f"[Agent] Found {len(new_urls)} new URLs. Streaming to Master...")
                r = requests.post(
                    f"{master_url}/submit_urls",
                    json={"id": worker_id, "urls": new_urls},
                    timeout=10
                )
                if r.status_code == 200:
                    offset_file.write_text(str(new_pos))
                    print(f"[Agent] Successfully streamed {len(new_urls)} URLs.")
        except Exception as e:
            print(f"[Agent] URL streaming failed: {e}")

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
        
    # 3. Enter real-time telemetry streaming and command pulling loop
    print("[Agent] Entering telemetry update loop.")
    offset_file = Path(".agent_offset.txt")
    
    while True:
        # Step A: Stream any newly found URLs to Master
        stream_new_urls(master_url, worker_id, offset_file)
        
        # Step B: Load current local state and push as heartbeat to Master
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
                    cmd = res_data.get("command")
                    if cmd:
                        print(f"[Agent] Received command from Master: {cmd}")
                        # Route command to local Flask worker app running on port 5000
                        try:
                            # Map 'release' command to local 'start' API to resume crawling
                            local_action = "start" if cmd in ("start", "release") else cmd
                            res = requests.post(f"http://localhost:5000/{local_action}", json={}, timeout=5)
                            print(f"[Agent] Forwarded local /{local_action} endpoint: {res.status_code}")
                        except Exception as lex:
                            print(f"[Agent] Local worker command delivery failed: {lex}")
            except Exception as e:
                print(f"[Agent] Heartbeat update to Master failed: {e}")
                
        time.sleep(5)

if __name__ == "__main__":
    main()
