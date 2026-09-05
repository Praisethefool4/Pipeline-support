import os
import math

def split_file(filename, chunks=19):
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
