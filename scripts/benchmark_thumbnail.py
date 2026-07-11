import time
import base64
import statistics
from pathlib import Path
import fitz  # PyMuPDF

def find_large_pdf(books_dir: Path) -> Path | None:
    for p in books_dir.glob("*.pdf"):
        if p.stat().st_size > 30 * 1024 * 1024:
            return p
    return None

def benchmark_scan_pdf(pdf_path: Path):
    print(f"=== Benchmarking Scan PDF (File Size: {pdf_path.stat().st_size / 1024 / 1024:.2f} MB) ===")
    
    # 1. fitz.open() のみの時間計測 (初回 vs 2回目以降)
    open_times = []
    for i in range(10):
        t0 = time.perf_counter()
        doc = fitz.open(str(pdf_path))
        doc.close()
        t1 = time.perf_counter()
        open_times.append((t1 - t0) * 1000) # ms
        
    first_open = open_times[0]
    subsequent_opens = open_times[1:]
    avg_subsequent_open = sum(subsequent_opens) / len(subsequent_opens)
    
    print(f"fitz.open() - First run: {first_open:.2f} ms")
    print(f"fitz.open() - Subsequent runs (Avg): {avg_subsequent_open:.2f} ms")
    print(f"fitz.open() - Min: {min(open_times):.2f} ms | Median: {statistics.median(open_times):.2f} ms | Max: {max(open_times):.2f} ms\n")
    
    doc = fitz.open(str(pdf_path))
    text_page = None
    image_page = None
    
    for page_num in range(min(100, doc.page_count)):
        page = doc[page_num]
        text = page.get_text().strip()
        if len(text) > 800 and text_page is None:
            text_page = page_num + 1
        elif 10 < len(text) < 150 and image_page is None:
            image_page = page_num + 1
            
    if text_page is None:
        text_page = 10
    if image_page is None:
        image_page = 20
    doc.close()
    
    # zoom=1.0, Q=85 に絞って詳細統計を出す
    zoom, quality = 1.0, 85
    for name, page_num in [("Text Page", text_page), ("Image Page", image_page)]:
        print(f"--- {name} (p.{page_num}) - Details ---")
        times = []
        sizes = []
        for i in range(10):
            t0 = time.perf_counter()
            doc = fitz.open(str(pdf_path))
            matrix = fitz.Matrix(zoom, zoom)
            page = doc[page_num - 1]
            pix = page.get_pixmap(matrix=matrix)
            data = pix.tobytes("jpeg", jpg_quality=quality)
            doc.close()
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)
            sizes.append(len(data))
            
        print(f"Total Time - First run: {times[0]:.2f} ms")
        print(f"Total Time - Subsequent runs (Avg): {sum(times[1:]) / len(times[1:]):.2f} ms")
        print(f"Total Time - Min: {min(times):.2f} ms | Median: {statistics.median(times):.2f} ms | Max: {max(times):.2f} ms")
        print(f"Output Size: {sizes[0] / 1024:.2f} KB (Base64: {len(base64.b64encode(data)) / 1024:.2f} KB)\n")

if __name__ == "__main__":
    books_dir = Path("/data/books")
    large_pdf = find_large_pdf(books_dir)
    if large_pdf:
        benchmark_scan_pdf(large_pdf)
    else:
        print("No large PDF (>30MB) found in /data/books")
