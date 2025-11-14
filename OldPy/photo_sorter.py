# File: photo_sorter.py (Updated with match_mode support)
# Verion 08.01.01.01 dated 20250924



import os
import cv2
import numpy as np
import shutil
import uuid
from tkinter import (
    ttk, 
    filedialog, 
    messagebox, 
    simpledialog, 
    colorchooser
)

from insightface.app import FaceAnalysis
from sklearn.metrics.pairwise import cosine_similarity
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from reference_db import (
    get_all_references,
    log_match_result,
    get_threshold_for_label,
    purge_missing_references
)
import urllib.request
import zipfile

from settings_manager import SettingsManager

# Global cache
ref_embeddings = {}

# -------------------- New: sort results model --------------------
@dataclass
class SortResult:
    """
    Metadata-only result for a single image.
    """
    path: str
    labels_set: set[str] = field(default_factory=set)
    best_label: Optional[str] = None
    label_scores: Dict[str, float] = field(default_factory=dict)
    status: str = "unmatched"   # "matched" | "unmatched" | "error"
    error: Optional[str] = None

 
def _update_catalog_entry(catalog: Optional[Dict[str, dict]],
                          img_path: str,
                          res: SortResult) -> None:
    """
    Optional convenience to update an in-memory catalog if the caller passes one.
    Expected catalog shape (suggested):
      catalog[path] = {
         "label": str|None,
         "best_label": str|None,
         "labels": set[str],
         "scores": dict[label->score],
         "status": "matched|unmatched|error"
      }
    """
    if catalog is None:
        return
    entry = catalog.setdefault(img_path, {})
    entry["label"] = res.best_label
    entry["best_label"] = res.best_label
    entry["labels"] = set(res.labels_set)
    entry["scores"] = dict(res.label_scores)
    entry["status"] = res.status


# ---- Global model cache (loaded once per process) --------------

_MODEL_CACHE = {
    "app": None,         # cached FaceAnalysis instance
    "model_dir": None,   # path it was built from
    "providers": None,   # ORT providers used
}

REQUIRED_MODEL_FILES = [
    "det_10g.onnx",
    "w600k_r50.onnx",
    "1k3d68.onnx",
    "2d106det.onnx",
    "genderage.onnx"
]

MODEL_LIBRARY = {}

class OfflineModelMissingError(RuntimeError):
    pass

def clear_model_cache():
    _MODEL_CACHE["app"] = None
    _MODEL_CACHE["model_dir"] = None
    _MODEL_CACHE["providers"] = None

# What we expect inside the buffalo_l model set (insightface variants differ a bit)
_REQUIRED_CORE = {"w600k_r50.onnx"}
# At least one detector (old vs new naming)
_REQUIRED_DET_ANY = {"det_10g.onnx", "scrfd_10g_bnkps.onnx"}
# Optional extras commonly used by your logs
_OPTIONAL = {"2d106det.onnx", "1k3d68.onnx", "genderage.onnx"}


def release_model():
    global _MODEL_CACHE
    if _MODEL_CACHE["app"]:
        try:
            del _MODEL_CACHE["app"]
            _MODEL_CACHE["app"] = None
            print("✅ InsightFace released.")
        except Exception as e:
            print(f"⚠️ Error releasing model: {e}")


#def download_model_files(target_folder):
#    MODEL_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

#    os.makedirs(target_folder, exist_ok=True)
#    zip_path = os.path.join(target_folder, "buffalo_l.zip")

#    try:
#        print(f"⬇️ Downloading model to: {zip_path}")
#        urllib.request.urlretrieve(MODEL_URL, zip_path)

#        # Extract zip
#        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
#            zip_ref.extractall(target_folder)
#
#        os.remove(zip_path)
#        print("✅ Model download and extraction complete.")
#
#    except Exception as e:
#        raise RuntimeError(f"Failed to download models: {e}")


def _buffalo_candidates(root_dir):
    # We support either:
    #   <root>/buffalo_l/*.onnx
    #   <root>/models/buffalo_l/*.onnx   (insightface’s default)
    return [
        os.path.join(root_dir, "buffalo_l"),
        os.path.join(root_dir, "models", "buffalo_l"),
    ]


def _list_files(folder):
    try:
        return set(f for f in os.listdir(folder) if f.lower().endswith(".onnx"))
    except Exception:
        return set()
        

def load_model_library(offline_only=False):
    global MODEL_LIBRARY
    SETTINGS = SettingsManager()
    
    # Step 1: Load path from settings
    path = SETTINGS.get("model_library_path", "")
    
    if not path or not os.path.isdir(path):
        messagebox.showwarning(
            "Model Path Not Set",
            "Model library path is not set or does not exist. Please choose the correct folder."
        )
        path = filedialog.askdirectory(title="Select Model Library Folder")
        if not path:
            return False
        SETTINGS.set("model_library_path", path)
        SETTINGS.save()

    # Step 2: Check model files in selected folder (no subfolder nesting)
    missing = [f for f in REQUIRED_MODEL_FILES if not os.path.isfile(os.path.join(path, f))]

    if missing:
        msg = (
            f"Required model files are missing in:\n\n{path}\n\n"
            f"Missing: {', '.join(missing)}\n\n"
            f"Would you like to download them now?"
        )
        if offline_only:
            messagebox.showerror("Offline Mode", "Some model files are missing, and you are in offline-only mode.")
            return False
        
        user_choice = messagebox.askyesno("Model Files Missing", msg)
        if user_choice:
            try:
                from model_downloader import download_model_files  # Ensure you define this in your app
                download_model_files(path)  # This should unzip or place ONNX files directly into `path`
            except Exception as e:
                messagebox.showerror("Download Failed", f"Could not download model files: {e}")
                return False
        else:
            return False

    # Step 3: Load model structure — just store file paths or pass to inference loader later
    try:
        MODEL_LIBRARY = {
            name: os.path.join(path, name)
            for name in REQUIRED_MODEL_FILES
        }
        print(f"✅ Model library loaded from: {path}")
        return True

    except Exception as e:
        print(f"⚠️ Failed to initialize model library: {e}")
        messagebox.showerror("Model Load Failed", str(e))
        MODEL_LIBRARY = None
        return False


def is_buffalo_ready(buffalo_root: str):
    """
    Ensure <buffalo_root> (buffalo_l folder) has the ONNX files.
    """
    files = set(f for f in os.listdir(buffalo_root) if f.lower().endswith(".onnx"))
    missing = []
    if "w600k_r50.onnx" not in files:
        missing.append("w600k_r50.onnx")
    if not any(x in files for x in ("det_10g.onnx", "scrfd_10g_bnkps.onnx")):
        missing.append("detector model")
    return (len(missing) == 0), missing, buffalo_root



def get_model_dir():
    """
    Always return the buffalo_l root folder, never parent or /models.
    """
    settings = SettingsManager()
    path = settings.get("model_library_path", "")
    if not path:
        raise RuntimeError("Model directory not set in settings.")
    if not os.path.isdir(path):
        raise RuntimeError(f"Model directory not found: {path}")
    return os.path.normpath(path)
    

def get_buffalo_model(model_dir, providers=None, log_callback=print):
    """
    Return a cached insightface.FaceAnalysis('buffalo_l') instance.
    - Detects available onnxruntime providers (CPU/GPU) if not specified.
    - Prepares once with det_size=(640,640).
    - Reuses the same object for future calls (same model_dir/providers).
    """
    
    global _MODEL_CACHE

    # Normalize args
    model_dir = os.path.normpath(model_dir)

    # HARD PRE-CHECK to avoid any auto-download in offline mode
    try:
        settings = SettingsManager()
        ok, missing, resolved = is_buffalo_ready(model_dir)
        if not ok and settings.get("offline_mode", False):
            where = resolved or model_dir
            msg = ("Offline mode: buffalo_l model files are missing.\n"
                       f"Checked: {where}\nMissing: {', '.join(missing)}")
            log_callback(f"⛔ {msg}")
            return None
    except Exception as e:
        # If pre-check itself fails, be safe in offline mode
        try:
            if SettingsManager().get("offline_mode", False):
                log_callback(f"⛔ Offline mode model check failed: {e}")
                return None
        except Exception:
            pass    

    # Auto-detect providers if not given
    if providers is None:
        try:
            import onnxruntime as ort
            avail = ort.get_available_providers()
        except Exception:
            avail = ["CPUExecutionProvider"]
        providers = ["CUDAExecutionProvider"] if "CUDAExecutionProvider" in avail else ["CPUExecutionProvider"]

    # If already cached with same settings, reuse
    
    if (
        _MODEL_CACHE["app"] is not None and
        _MODEL_CACHE["model_dir"] == model_dir and
        _MODEL_CACHE["providers"] == tuple(providers)
    ):
        return _MODEL_CACHE["app"]

    # (Re)initialize
    app = _init_your_existing_buffalo(model_dir, providers, log_callback)
    if app is None:
        return None
    _MODEL_CACHE.update({
        "app": app,
        "model_dir": model_dir,
        "providers": tuple(providers),
    })
    return app


def _init_your_existing_buffalo(model_dir, providers=None, log_callback=print):
    """
    Initialize FaceAnalysis with root=<buffalo_l>.
    """
    try:
        log_callback(f"📦 Using buffalo_l root: {model_dir}")
        if not os.path.isdir(model_dir):
            raise RuntimeError(f"Missing buffalo_l dir: {model_dir}")

        # 🚨 Your installed InsightFace does NOT support 'providers'
        app = FaceAnalysis(name="buffalo_l", root=model_dir)

        # Decide ctx_id: 0 = GPU, -1 = CPU
        use_cuda = (isinstance(providers, (list, tuple)) and "CUDAExecutionProvider" in providers)
        ctx_id = 0 if use_cuda else -1

        app.prepare(ctx_id=ctx_id, det_size=(640, 640))
        return app
    except Exception as e:
        log_callback(f"❌ Failed to initialize FaceAnalysis: {e}")
        return None


def _build_safe_destination(out_folder, filename, keep_original_filenames=True):
    """
    Returns a destination path inside out_folder that does not overwrite existing files.
    If keep_original_filenames=True -> keep name, add _2, _3... on collision.
    Otherwise -> prefix an 8-char uuid.
    """
    os.makedirs(out_folder, exist_ok=True)
    name, ext = os.path.splitext(filename)

    if not keep_original_filenames:
        return os.path.join(out_folder, f"{uuid.uuid4().hex[:8]}_{filename}")

    candidate = os.path.join(out_folder, filename)
    if not os.path.exists(candidate):
        return candidate

    i = 2
    while True:
        candidate = os.path.join(out_folder, f"{name}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def _copy_one(src_path, dest_folder, filename, keep_original_filenames, log_callback):
    dst = _build_safe_destination(dest_folder, filename, keep_original_filenames)
    shutil.copy2(src_path, dst)
    log_callback(f"📄 Copied {os.path.basename(src_path)} → {dest_folder} as {os.path.basename(dst)}")
    return dst


def _move_one(src_path, dest_folder, filename, keep_original_filenames, log_callback):
    dst = _build_safe_destination(dest_folder, filename, keep_original_filenames)
    shutil.move(src_path, dst)
    log_callback(f"📦 Moved {os.path.basename(dst)} into {dest_folder}")
    return dst

# ---------------USES CACHE -------------------------------
def load_model(model_dir, log_callback):
    # Back-compat wrapper that uses the cached model
    app = get_buffalo_model(model_dir, providers=None, log_callback=log_callback)
    return app


def build_reference_embeddings_for_labels(db_path, model_dir, labels, log_callback=print):
    """
    Rebuild embeddings only for the given label(s).
    - Updates global ref_embeddings[label] in-place.
    - Removes the label from ref_embeddings if it has no valid faces anymore.
    """
    global ref_embeddings

    # Resolve centrally if not provided
    if model_dir is None:
        try:
            model_dir = get_model_dir()
        except Exception as e:
            log_callback(f"❌ Could not resolve model path: {e}")
            return

    log_callback(f"🧠 Using model library: {model_dir}")

    # Normalize labels into a set[str]
    if labels is None:
        log_callback("⚠️ No labels requested for partial rebuild.")
        return
    if isinstance(labels, str):
        labels = [labels]
    target = set(labels)
    if not target:
        log_callback("⚠️ No labels requested for partial rebuild.")
        return

    # Clean dead paths (safe to run every time)
    try:
        removed = purge_missing_references()
        if removed:
            log_callback(f"🧹 Cleaned {removed} dead reference entries.")
    except Exception as e:
        log_callback(f"⚠️ DB cleanup skipped: {e}")

    app = get_buffalo_model(model_dir, log_callback=log_callback)
    if app is None:
        return

    try:
        all_refs = get_all_references()  # [(id, label, path), ...]
    except Exception as e:
        log_callback(f"❌ Failed to fetch references from DB: {e}")
        return

    # Group references by label (filter early)
    refs_by_label = {}
    for _id, lbl, path in all_refs:
        if lbl in target:
            refs_by_label.setdefault(lbl, []).append(path)


    # Recompute each requested label
    for lbl in target:
        paths = refs_by_label.get(lbl, [])
        if not paths:
            # No references → remove from embeddings if present
            if lbl in ref_embeddings:
                del ref_embeddings[lbl]
                log_callback(f"ℹ️ '{lbl}': no references left → removed from embeddings.")
            continue

        embeddings = []
        for img_path in paths:
            try:
                if not os.path.isfile(img_path):
                    log_callback(f"⚠️ Missing reference file, skipping: {img_path}")
                    continue
                img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("Image not readable")
                faces = app.get(img)
                if not faces:
                    log_callback(f"⚠️ No face found in reference: {img_path}")
                    continue
                vecs = [f.embedding for f in faces]
                embeddings.append(np.mean(vecs, axis=0))
                log_callback(f"✔️ Embedded '{lbl}' from {img_path}")
            except Exception as e:
                log_callback(f"❌ Error processing {img_path}: {e}")

        if embeddings:
            ref_embeddings[lbl] = np.mean(embeddings, axis=0)
        else:
            if lbl in ref_embeddings:
                del ref_embeddings[lbl]
            log_callback(f"⚠️ '{lbl}': no valid embeddings after rebuild.")

def build_reference_embeddings_from_db(db_path, model_dir, log_callback=None):
    """
    Full rebuild for ALL labels (Tools → Rebuild Embeddings).
    """
    global ref_embeddings
    ref_embeddings.clear()
    
    if not model_dir or not os.path.isdir(model_dir):
        log_callback(f"❌ Invalid model path: {model_dir}")
        return
        
    # Resolve centrally if not provided
    if model_dir is None:
        try:
#            model_dir = get_model_dir()
            SettingsManager().get("model_library_path")
        except Exception as e:
            log_callback(f"❌ Could not resolve model path: {e}")
            return

    log_callback(f"🧠 Using model library: {model_dir}")
    

    # Clean dead paths
    try:
        removed = purge_missing_references()
        if removed:
            log_callback(f"🧹 Cleaned {removed} dead reference entries.")
    except Exception as e:
        log_callback(f"⚠️ DB cleanup skipped: {e}")

    app = get_buffalo_model(model_dir, log_callback=log_callback)
    if app is None:
        return

    try:
        references = get_all_references()
    except Exception as e:
        log_callback(f"❌ Failed to fetch references from DB: {e}")
        return

    if not references:
        log_callback("⚠️ No references found in DB. Add some in the GUI first.")
        return

    tmp = {}
    for _id, label, img_path in references:
        try:
            if not os.path.isfile(img_path):
                log_callback(f"⚠️ Missing reference file, skipping: {img_path}")
                continue
            img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("Image not readable")
            faces = app.get(img)
            if not faces:
                log_callback(f"⚠️ No face found in reference: {img_path}")
                continue
            vecs = [f.embedding for f in faces]
            tmp.setdefault(label, []).append(np.mean(vecs, axis=0))
            log_callback(f"✔️ Embedded '{label}' from {img_path}")
        except Exception as e:
            log_callback(f"❌ Error processing {img_path}: {e}")

    for label, vecs in tmp.items():
        if vecs:
            ref_embeddings[label] = np.mean(vecs, axis=0)

    if not ref_embeddings:
        log_callback("⚠️ No valid embeddings were built. Check your reference images.")


#def sort_photos_with_embeddings_from_folder_using_db(
#    inbox_dir, 
#    output_dir, 
#    unmatched_dir, 
#    db_path, 
#    log_callback, 
#    match_mode="multi",          # "multi", "best", "manual"
#    move_files=True,             # kept for compatibility; we honor your plan below
#    keep_original_filenames=True,
#    stop_event=None,
#    model_dir=None,    
    
#):

def sort_photos_with_embeddings_from_folder_using_db(
    inbox_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    unmatched_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    log_callback=print,
    match_mode: str = "multi",        # "multi", "best", "manual"
    move_files: bool = True,          # deprecated; use apply_physical
    keep_original_filenames: bool = True,
    stop_event=None,
    model_dir: Optional[str] = None,
    *,
    image_paths: Optional[Iterable[str]] = None,
    catalog: Optional[Dict[str, dict]] = None,
    apply_physical: bool = False,
) -> List[SortResult]:
#    """
#    Behavior per plan:
#      - BEST: always MOVE to the best label.
#      - MULTI: COPY to all other matched labels, then MOVE to the BEST label.
#      - MANUAL: placeholder → send to unmatched for now.
#    Filenames are preserved; on collision we add _2, _3, ...
#    """
    """
    Two modes:
      1) Metadata-only (default): does NOT touch files; returns a list[SortResult] and
         optionally updates a provided `catalog` mapping by path.
      2) Physical apply (apply_physical=True): preserves legacy behavior by performing
         move/copy operations based on match_mode and keep_original_filenames.

    When metadata-only:
      - best_label / labels_set / label_scores are computed and returned.
      - status is "matched" or "unmatched".
      - No files are moved or copied.
    """
    
#    # Normalize and verify
#    inbox_dir = os.path.normpath(inbox_dir)
#    output_dir = os.path.normpath(output_dir)
#    unmatched_dir = os.path.normpath(unmatched_dir)

    results: List[SortResult] = []

    # Prepare inputs
    if image_paths is not None:
        files_to_process: List[str] = [os.path.normpath(p) for p in image_paths if os.path.isfile(p)]
    else:
        if not inbox_dir:
            log_callback("❌ No inbox_dir or image_paths provided.")
            return results
        inbox_dir = os.path.normpath(inbox_dir)
        files_to_process = []
        for subdir, _, files in os.walk(inbox_dir):
            for f in files:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                    files_to_process.append(os.path.normpath(os.path.join(subdir, f)))

    if apply_physical:
        if not output_dir:
            raise ValueError("apply_physical=True requires output_dir.")
        output_dir = os.path.normpath(output_dir)
        unmatched_dir = os.path.normpath(unmatched_dir or os.path.join(output_dir, "_unmatched"))
        os.makedirs(unmatched_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        log_callback("🧱 Physical apply mode: files will be copied/moved.")
    else:
        log_callback("🧪 Metadata-only mode: no files will be modified.")
  

    if model_dir is None:
        model_dir = get_model_dir()
    app = get_buffalo_model(model_dir, log_callback=log_callback)
        
    def _should_stop():
        return (stop_event is not None) and stop_event.is_set()

#    if not os.path.isdir(inbox_dir):
#        log_callback(f"❌ Inbox folder does not exist: {inbox_dir}")
#        return
#
#    os.makedirs(unmatched_dir, exist_ok=True)
#    os.makedirs(output_dir, exist_ok=True)


    if app is None:
#        return
        return results

    log_callback(f"🔧 Match mode: {match_mode}")
#    log_callback(f"📁 Walking inbox: {inbox_dir}")
#
#    for subdir, _, files in os.walk(inbox_dir):
#        if _should_stop():
#            log_callback("⛔ Stop requested. Finishing current item and exiting…")
#            break
#        for file in files:
#            if _should_stop():
#                log_callback("⛔ Stop requested. Finishing current item and exiting…")
#                break
#            
#            if not file.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
#                continue
#
#            img_path = os.path.normpath(os.path.join(subdir, file))

    log_callback(f"📄 Processing {len(files_to_process)} image(s)")

    for img_path in files_to_process:
        if _should_stop():
            log_callback("⛔ Stop requested. Finishing current item and exiting…")
            break
        file = os.path.basename(img_path)
        
        if not os.path.isfile(img_path):
            log_callback(f"⚠️ Skipping missing file (not found): {img_path}")
#                continue
            results.append(SortResult(path=img_path, status="error", error="Missing file"))
            _update_catalog_entry(catalog, img_path, results[-1])    
            continue

            
            # Load + detect
                      
            try:
                img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("Image unreadable")

                faces = app.get(img)
                if not faces:
                    raise RuntimeError("No faces found")
            except Exception as e:
                log_callback(f"⚠️ Skipping {file}: {e}")
#                try:
#                    # move unreadable images to unmatched, keep name (use collision-safe)
#                    dst = _build_safe_destination(unmatched_dir, file, keep_original_filenames)
#                    shutil.move(img_path, dst)
#                    log_callback(f"↪︎ Moved to unmatched: {os.path.basename(dst)}")
#                except Exception as move_e:
#                    log_callback(f"⚠️ Could not move to unmatched: {move_e}")
#                continue

                # metadata-only: record error/unmatched; physical: attempt move to unmatched
                if apply_physical:
                    try:
                        dst = _build_safe_destination(unmatched_dir, file, keep_original_filenames)
                        shutil.move(img_path, dst)
                        log_callback(f"↪︎ Moved to unmatched: {os.path.basename(dst)}")
                    except Exception as move_e:
                        log_callback(f"⚠️ Could not move to unmatched: {move_e}")
                res = SortResult(path=img_path, status="unmatched", error=str(e))
                results.append(res)
                _update_catalog_entry(catalog, img_path, res)
                continue

            if _should_stop():
                log_callback("⛔ Stop requested. Finishing current item and exiting…")
                break
  

            if match_mode == "manual":
                log_callback(f"🛠️ Manual match mode not implemented yet for {file}")
#                try:
#                    dst = _build_safe_destination(unmatched_dir, file, keep_original_filenames)
#                    shutil.move(img_path, dst)
#                    log_callback(f"↪︎ Moved to unmatched: {os.path.basename(dst)}")
#                except Exception as move_e:
#                    log_callback(f"⚠️ Could not move to unmatched: {move_e}")
#                continue

                # Non-destructive: mark unmatched; Physical: move to unmatched
                if apply_physical:
                    try:
                        dst = _build_safe_destination(unmatched_dir, file, keep_original_filenames)
                        shutil.move(img_path, dst)
                        log_callback(f"↪︎ Moved to unmatched: {os.path.basename(dst)}")
                    except Exception as move_e:
                        log_callback(f"⚠️ Could not move to unmatched: {move_e}")
                res = SortResult(path=img_path, status="unmatched")
                results.append(res)
                _update_catalog_entry(catalog, img_path, res)
                continue


            if _should_stop():
                log_callback("⛔ Stop requested. Finishing current item and exiting…")
                break
  
            # Identify
            labels_set, best_label, _scores = identify_faces(faces, file, log_callback, match_mode)

            if not labels_set:
                log_callback(f"⚠️ No good match for {file}")
#                try:
#                    dst = _build_safe_destination(unmatched_dir, file, keep_original_filenames)
#                    shutil.move(img_path, dst)
#                    log_callback(f"↪︎ Moved to unmatched: {os.path.basename(dst)}")
#                except Exception as move_e:
#                    log_callback(f"⚠️ Could not move to unmatched: {move_e}")
#                continue

                if apply_physical:
                    try:
                        dst = _build_safe_destination(unmatched_dir, file, keep_original_filenames)
                        shutil.move(img_path, dst)
                        log_callback(f"↪︎ Moved to unmatched: {os.path.basename(dst)}")
                    except Exception as move_e:
                        log_callback(f"⚠️ Could not move to unmatched: {move_e}")
                res = SortResult(
                    path=img_path,
                    labels_set=set(),
                    best_label=None,
                    label_scores=_scores,
                    status="unmatched"
                )
                results.append(res)
                _update_catalog_entry(catalog, img_path, res)
                continue


#            # Distribute per requested mode:
#            if match_mode == "best":
#                distribute_to_labels(
#                    img_path, file, labels_set, best_label, output_dir, log_callback,
#                    keep_original_filenames=keep_original_filenames, mode="best"
#                )
#            elif match_mode == "multi":
#                # COPY to all other matches, then MOVE to best
#                distribute_to_labels(
#                    img_path, file, labels_set, best_label, output_dir, log_callback,
#                    keep_original_filenames=keep_original_filenames, mode="multi"
#                )
#            else:
#                # Fallback: treat as best
#                distribute_to_labels(
#                    img_path, file, labels_set, best_label, output_dir, log_callback,
#                    keep_original_filenames=keep_original_filenames, mode="best"
#                )

            if apply_physical:
                if match_mode == "best":
                    distribute_to_labels(
                        img_path, file, labels_set, best_label, output_dir, log_callback,
                        keep_original_filenames=keep_original_filenames, mode="best"
                    )
                elif match_mode == "multi":
                    # COPY to all other matches, then MOVE to best
                    distribute_to_labels(
                        img_path, file, labels_set, best_label, output_dir, log_callback,
                        keep_original_filenames=keep_original_filenames, mode="multi"
                    )
                else:
                    # Fallback: treat as best
                    distribute_to_labels(
                        img_path, file, labels_set, best_label, output_dir, log_callback,
                        keep_original_filenames=keep_original_filenames, mode="best"
                    )
                # In physical mode we don't need to return anything else for this image,
                # but still report a result line for UI completeness:
                res = SortResult(
                    path=img_path,
                    labels_set=set(labels_set),
                    best_label=best_label,
                    label_scores=_scores,
                    status="matched"
                )
                results.append(res)
                _update_catalog_entry(catalog, img_path, res)
            else:
                # metadata-only: just record the result (no file ops)
                res = SortResult(
                    path=img_path,
                    labels_set=set(labels_set),
                    best_label=best_label,
                    label_scores=_scores,
                    status="matched"
                )
                results.append(res)
                _update_catalog_entry(catalog, img_path, res)

    return results

def classify_photos_metadata_only(
    inbox_dir,
    db_path,
    log_callback,
    match_mode="best",
    stop_event=None,
    model_dir=None,
):
    """
    Metadata-only version:
    - Reads photos, runs embeddings, matches labels
    - Returns {photo_path: {"labels": set, "best_label": str|None, "scores": dict}}
    - Does NOT move/copy files
    """
    results = {}

    inbox_dir = os.path.normpath(inbox_dir)
    if model_dir is None:
        model_dir = get_model_dir()

    app = get_buffalo_model(model_dir, log_callback=log_callback)
    if app is None:
        return results

    log_callback(f"📁 Scanning inbox (metadata-only): {inbox_dir}")

    for subdir, _, files in os.walk(inbox_dir):
        for file in files:
            if stop_event and stop_event.is_set():
                log_callback("⛔ Stop requested. Exiting metadata classification.")
                return results

            if not file.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                continue

            img_path = os.path.join(subdir, file)
            try:
                img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("Unreadable")

                faces = app.get(img)
                if not faces:
                    log_callback(f"⚠️ No faces: {file}")
                    continue

                labels_set, best_label, scores = identify_faces(faces, file, log_callback, match_mode)
                results[img_path] = {
                    "labels": labels_set,
                    "best_label": best_label,
                    "scores": scores,
                }

            except Exception as e:
                log_callback(f"❌ Error processing {file}: {e}")
                continue

    return results


def identify_faces(faces, file, log_callback, match_mode):
    """
    Returns:
      labels_set: set[str] of matched labels (unique)
      best_label: str|None (the label with highest score overall)
      label_scores: dict[label] -> best score (max across faces)
    """
    label_scores = {}
    matches = []  # (label, score) per-face best

    for face in faces:
        embedding = face.embedding
        best_label = None
        best_score = 0.0

        for label, ref_emb in ref_embeddings.items():
            score = cosine_similarity([embedding], [ref_emb])[0][0]
            threshold = get_threshold_for_label(label)
            if score >= threshold and score > best_score:
                best_score = score
                best_label = label

        if best_label:
            matches.append((best_label, best_score))
            # keep the max score per label
            label_scores[best_label] = max(label_scores.get(best_label, 0.0), best_score)
            log_match_result(file, best_label, best_score, match_mode=match_mode)

    if not matches:
        return set(), None, label_scores

    labels_set = set(lbl for (lbl, _) in matches)
    # overall best label by score across faces
    best_label_overall = max(label_scores.items(), key=lambda kv: kv[1])[0]
    return labels_set, best_label_overall, label_scores


def copy_to_label_dirs(img_path, filename, labels, output_dir, log_callback):
    for label in labels:
        out_folder = os.path.join(output_dir, label)
        os.makedirs(out_folder, exist_ok=True)

        unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
        out_path = os.path.join(out_folder, unique_filename)

        try:
            shutil.copy(img_path, out_path)
            log_callback(f"📤 Copied {filename} → {label}")
        except Exception as e:
            log_callback(f"❌ Copy failed for {filename}: {e}")


def distribute_to_labels(img_path, filename, labels_set, best_label, output_dir, log_callback,
                         keep_original_filenames=True, mode="best"):
    """
    - mode == "best": move to best_label only.
    - mode == "multi": copy to every *other* label, then move original to best_label.
    """
    if not labels_set:
        return

    # safety
    if best_label is None:
        # fallback: just pick any deterministic label
        best_label = sorted(labels_set)[0]

    if mode == "best" or len(labels_set) == 1:
        # MOVE once to the best label
        dest_folder = os.path.join(output_dir, best_label)
        _move_one(img_path, dest_folder, filename, keep_original_filenames, log_callback)
        return

    # MULTI:
    # 1) Copy to all non-best labels (using the original path as source)
    others = [lbl for lbl in labels_set if lbl != best_label]
    for lbl in others:
        dest_folder = os.path.join(output_dir, lbl)
        try:
            _copy_one(img_path, dest_folder, filename, keep_original_filenames, log_callback)
        except Exception as e:
            log_callback(f"⚠️ Copy failed for {filename} → {lbl}: {e}")

    # 2) Move the original into the best label (final location)
    dest_folder = os.path.join(output_dir, best_label)
    try:
        _move_one(img_path, dest_folder, filename, keep_original_filenames, log_callback)
    except Exception as e:
        log_callback(f"❌ Move failed for {filename} → {best_label}: {e}")

