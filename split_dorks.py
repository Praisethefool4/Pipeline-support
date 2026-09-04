import os
import math

def split_file(filename, chunks=20):
    if not os.path.exists(filename):
        print(f"Error: {filename} not found.")
        return
    
    with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
        lines = [line.strip() for line in f if line.strip()]
    
    total = len(lines)
    chunk_size = math.ceil(total / chunks)
    print(f"Splitting {total} dorks into {chunks} chunks (~{chunk_size} per chunk)...")
    
    os.makedirs('chunks', exist_ok=True)
    for i in range(chunks):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk_lines = lines[start:end]
        
        chunk_file = f"chunks/dorks_{i+1:02d}.txt"
        with open(chunk_file, 'w', encoding='utf-8') as out_f:
            out_f.write('\n'.join(chunk_lines) + '\n')

if __name__ == "__main__":
    split_file('my_dorks.txt', 20)
