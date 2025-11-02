# meta_backfill_pool.py
# Version 01.1.01.06 dated 20251026
#!/usr/bin/env python3


"""
meta_backfill_pool.py

Persistent metadata backfill supervisor + worker pool.
Uses ReferenceDB API (in reference_db.py) for all DB operations.

Usage:
  python meta_backfill_pool.py --workers 4 --timeout 8 --batch 200 --limit 0

Run this as a separate detached process from the GUI for production-grade backfill.
"""
import sys
import time
import json
import argparse
import traceback
import os
from pathlib import Path
from multiprocessing import Process, Queue, Event, cpu_count
import multiprocessing

# Import ReferenceDB from repo root (assumes this script runs from project root)
try:
    from reference_db import ReferenceDB
except Exception as e:
    print("Failed to import reference_db.ReferenceDB:", e)
    raise

# Worker function that runs inside a child process
def worker_loop_1st(worker_id: int, task_q: Queue, result_q: Queue, stop_event: Event):
    """
    Persistent worker process. Pulls file paths from task_q, extracts metadata using Pillow
    and pushes result dicts to result_q.
    """
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        from PIL import Image, ExifTags
    except Exception as e:
        # If PIL is not available, fail fast
        result_q.put({"path": None, "ok": False, "error": f"PIL import failed: {e}"})
        return

    # optional HEIF support
    try:
        import pillow_heif  # noqa
    except Exception:
        pass

    def extract(path):
        out = {"path": str(path)}
        try:
            with Image.open(path) as im:
                w, h = im.size
                out["width"] = int(w)
                out["height"] = int(h)
                date_taken = None
                try:
                    exif = im.getexif()
                except Exception:
                    exif = None
                if exif:
                    TAGS = ExifTags.TAGS
                    def get_by_name(name):
                        for k, v in exif.items():
                            if TAGS.get(k) == name:
                                return v
                        return None
                    dt = get_by_name("DateTimeOriginal") or get_by_name("DateTimeDigitized") or get_by_name("DateTime")
                    if dt:
                        s = str(dt)
                        parts = s.split(" ", 1)
                        if parts:
                            d = parts[0].replace(":", "-", 2)
                            rest = parts[1] if len(parts) > 1 else ""
                            date_taken = (d + (" " + rest if rest else "")).strip()
                        else:
                            date_taken = s
                out["date_taken"] = date_taken
                out["ok"] = True
        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)
        return out

    while not stop_event.is_set():
        try:
            p = task_q.get(timeout=0.5)
        except Exception:
            continue
        if p is None:
            break
        res = extract(p)
        res["elapsed"] = 0.0
        try:
            result_q.put(res)
        except Exception:
            pass

# Worker function that runs inside a child process
def worker_loop(worker_id: int, task_q: Queue, result_q: Queue, stop_event: Event):
    """
    Persistent worker process. Pulls file paths from task_q, extracts metadata using Pillow
    and pushes result dicts to result_q.
    """
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        from PIL import Image, ExifTags
    except Exception as e:
        # If PIL is not available, fail fast
        result_q.put({"path": None, "ok": False, "error": f"PIL import failed: {e}"})
        return

    # optional HEIF support
    try:
        import pillow_heif  # noqa
    except Exception:
        pass

    def extract(path):
        out = {"path": str(path)}
        try:
            with Image.open(path) as im:
                w, h = im.size
                out["width"] = int(w)
                out["height"] = int(h)
                date_taken = None
                try:
                    exif = im.getexif()
                except Exception:
                    exif = None
                if exif:
                    TAGS = ExifTags.TAGS
                    def get_by_name(name):
                        for k, v in exif.items():
                            if TAGS.get(k) == name:
                                return v
                        return None
                    dt = get_by_name("DateTimeOriginal") or get_by_name("DateTimeDigitized") or get_by_name("DateTime")
                    if dt:
                        s = str(dt)
                        parts = s.split(" ", 1)
                        if parts:
                            d = parts[0].replace(":", "-", 2)
                            rest = parts[1] if len(parts) > 1 else ""
                            date_taken = (d + (" " + rest if rest else "")).strip()
                        else:
                            date_taken = s
                out["date_taken"] = date_taken
                out["ok"] = True
        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)
        return out

    while not stop_event.is_set():
        try:
            p = task_q.get(timeout=0.5)
        except Exception:
            continue
        if p is None:
            break
        res = extract(p)
        res["elapsed"] = 0.0
        try:
            result_q.put(res)
        except Exception:
            pass

class PersistentPool:
    def __init__(self, workers=4, timeout=8.0):
        self.workers = max(1, min(int(workers), cpu_count() * 2))
        self.timeout = float(timeout)
        self.task_queues = []
        self.procs = []
        self.result_q = multiprocessing.Queue(maxsize=8192)
        self.stop_event = multiprocessing.Event()
        for i in range(self.workers):
            q = multiprocessing.Queue()
            p = Process(target=worker_loop, args=(i, q, self.result_q, self.stop_event), daemon=True)
            p.start()
            self.task_queues.append(q)
            self.procs.append(p)

    def submit_round_robin(self, paths):
        n = len(self.task_queues)
        i = 0
        for p in paths:
            try:
                self.task_queues[i % n].put(p)
            except Exception:
                pass
            i += 1

    def drain_results(self, max_items=200):
        out = []
        for _ in range(max_items):
            try:
                r = self.result_q.get_nowait()
                out.append(r)
            except Exception:
                break
        return out

    def shutdown(self):
        self.stop_event.set()
        for q in self.task_queues:
            try:
                q.put(None)
            except Exception:
                pass
        for p in self.procs:
            try:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=1.0)
            except Exception:
                pass

def controller_1st(workers=4, timeout=8.0, batch=200, limit=0, dry_run=False, max_retries=3):
    db = ReferenceDB()
    # Ensure DB has the metadata columns
    try:
        db.ensure_metadata_columns()
    except Exception:
        pass

    to_proc = db.get_images_missing_metadata(limit=limit or None, max_failures=max_retries)
    total = len(to_proc)
    print(f"[meta_backfill] found {total} images needing metadata")
    if total == 0:
        return 0

    pool = PersistentPool(workers=workers, timeout=timeout)
    pool.submit_round_robin(to_proc)

    processed = 0
    start = time.time()
    try:
        while True:
            results = pool.drain_results(max_items=batch * 2)
            if results:
                for r in results:
                    processed += 1
                    if r.get("ok"):
                        if not dry_run:
                            db.mark_metadata_success(r["path"], r.get("width"), r.get("height"), r.get("date_taken"))
                    else:
                        if not dry_run:
                            db.mark_metadata_failure(r["path"], error=r.get("error"), max_retries=max_retries)
                if processed % 10 == 0 or processed == total:
                    elapsed = time.time() - start
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    print(f"[meta_backfill] processed {processed}/{total} ({rate:.2f}/s)")
            # exit condition
            if processed >= total and pool.result_q.empty():
                break
            time.sleep(0.3)
    finally:
        pool.shutdown()
    elapsed = time.time() - start
    print(f"[meta_backfill] DONE processed={processed} total={total} elapsed={elapsed:.1f}s")
    return 0

def parse_args_1st(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-retries", type=int, default=3)
    return ap.parse_args(argv)

#if __name__ == "__main__":
#    args = parse_args()
#    rc = controller(workers=args.workers, timeout=args.timeout, batch=args.batch, limit=args.limit, dry_run=args.dry_run, max_retries=args.max_retries)
#    sys.exit(rc)
    

def controller(workers=4, timeout=8.0, batch=200, limit=0, dry_run=False, max_retries=3, quiet=False):
    db = ReferenceDB()
    # Ensure DB has the metadata columns
    try:
        db.ensure_metadata_columns()
    except Exception:
        pass

    to_proc = db.get_images_missing_metadata(limit=limit or None, max_failures=max_retries)
    total = len(to_proc)
    if not quiet:
        print(f"[meta_backfill] found {total} images needing metadata")
    if total == 0:
        return 0

    pool = PersistentPool(workers=workers, timeout=timeout)
    pool.submit_round_robin(to_proc)

    processed = 0
    start = time.time()
    try:
        while True:
            results = pool.drain_results(max_items=batch * 2)
            if results:
                for r in results:
                    processed += 1
                    if r.get("ok"):
                        if not dry_run:
                            db.mark_metadata_success(r["path"], r.get("width"), r.get("height"), r.get("date_taken"))
                    else:
                        if not dry_run:
                            db.mark_metadata_failure(r["path"], error=r.get("error"), max_retries=max_retries)
                if (processed % 10 == 0 or processed == total) and not quiet:
                    elapsed = time.time() - start
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    print(f"[meta_backfill] processed {processed}/{total} ({rate:.2f}/s)")
            # exit condition
            if processed >= total and pool.result_q.empty():
                break
            time.sleep(0.3)
    finally:
        pool.shutdown()
    elapsed = time.time() - start
    if not quiet:
        print(f"[meta_backfill] DONE processed={processed} total={total} elapsed={elapsed:.1f}s")
    return 0

def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--quiet", "--silent", action="store_true", dest="quiet",
                    help="Suppress console output (quiet mode). Useful when launching detached.")
    return ap.parse_args(argv)

if __name__ == "__main__":
    args = parse_args()
    rc = controller(
        workers=args.workers,
        timeout=args.timeout,
        batch=args.batch,
        limit=args.limit,
        dry_run=args.dry_run,
        max_retries=args.max_retries,
        quiet=args.quiet
    )
    sys.exit(rc)