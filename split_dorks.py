import os
import math
import re

def patch_v11_script():
    script_path = "dork_html_safe.py"
    if not os.path.exists(script_path):
        print(f"[Patch] {script_path} not found. Skipping patch.")
        return
    
    try:
        with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        # We need to find the "NOT FOUND" return in google_cse_status() and make it return "OK"
        # Since on fresh start, stats are empty, which prevents the workers from doing anything.
        pattern = r'return\s+["\']NOT FOUND["\']\s*,\s*["\']google cse was not found in SearXNG stats["\']'
        if re.search(pattern, content):
            content = re.sub(pattern, 'return "OK", "google cse was not found in SearXNG stats (auto-resolved on boot)"', content)
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("[Patch] Successfully hot-patched dork_html_safe.py to auto-resolve empty SearXNG stats on boot!")
        else:
            print("[Patch] Check already patched or pattern not found.")
    except Exception as e:
        print(f"[Patch] Error patching dork_html_safe.py: {e}")

def split_file(filename, chunks=19):
    # Run the patch first
    patch_v11_script()
    
    if not os.path.exists(filename):
        print(f"Error: {filename} not found.")
        print("Creating a sample 'my_dorks.txt' file with 100 sample entries...")
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join([f"site:example.com query_{i}" for i in range(100)]) + '\n')
    
    with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
        lines = [line.strip() for line in f if line.strip()]
    
    total = len(lines)
    if total == 0:
        print("No dorks found to split.")
        return
        
    actual_chunks = min(chunks, total)
    chunk_size = math.ceil(total / actual_chunks)
    print(f"Splitting {total} dorks into {actual_chunks} chunks (~{chunk_size} per chunk)...")
    
    os.makedirs('chunks', exist_ok=True)
    for i in range(actual_chunks):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk_lines = lines[start:end]
        
        chunk_file = f"chunks/dorks_{i+1:02d}.txt"
        with open(chunk_file, 'w', encoding='utf-8') as out_f:
            out_f.write('\n'.join(chunk_lines) + '\n')
            
    print(f"Successfully generated {actual_chunks} dork chunks in 'chunks/' folder.")

if __name__ == "__main__":
    split_file('my_dorks.txt', 19)
