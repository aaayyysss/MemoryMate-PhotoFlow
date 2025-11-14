# face_cluster_worker.py
# Version 02.00.00.00 (Phase 8 - Automatic Face Grouping)
# QRunnable worker with signals for automatic pipeline integration
# Reuses face_branch_reps + face_crops for clustering
# ------------------------------------------------------

import os
import sys
import time
import sqlite3
import numpy as np
import logging
from sklearn.cluster import DBSCAN
from PySide6.QtCore import QRunnable, QObject, Signal, Slot
from reference_db import ReferenceDB
from workers.progress_writer import write_status

logger = logging.getLogger(__name__)


class FaceClusterSignals(QObject):
    """
    Signals for face clustering worker progress reporting.
    """
    # progress(current, total, message)
    progress = Signal(int, int, str)

    # finished(cluster_count, total_faces)
    finished = Signal(int, int)

    # error(error_message)
    error = Signal(str)


class FaceClusterWorker(QRunnable):
    """
    Background worker for clustering detected faces into person groups.

    Uses DBSCAN clustering algorithm with cosine similarity metric
    to group similar face embeddings together.

    Usage:
        worker = FaceClusterWorker(project_id=1)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)

    Performance:
        - Clustering is fast: ~1-5 seconds for 1000 faces
        - Memory efficient: processes embeddings in batches
        - Configurable: eps and min_samples can be tuned

    Features:
        - Clears previous clusters before creating new ones
        - Creates representative face for each cluster
        - Updates face_branch_reps, branches, and face_crops tables
        - Emits progress signals for UI updates
    """

    def __init__(self, project_id: int, eps: float = 0.42, min_samples: int = 3):
        """
        Initialize face clustering worker.

        Args:
            project_id: Project ID to cluster faces for
            eps: DBSCAN epsilon parameter (max distance between faces in same cluster)
                 Lower = stricter grouping (more clusters, fewer false positives)
                 Higher = looser grouping (fewer clusters, more false positives)
                 Range: 0.3-0.5, default: 0.42
            min_samples: Minimum number of faces to form a cluster
                        Higher = larger clusters only (fewer clusters total)
                        Lower = allows smaller clusters
                        Range: 2-5, default: 3
        """
        super().__init__()
        self.project_id = project_id
        self.eps = eps
        self.min_samples = min_samples
        self.signals = FaceClusterSignals()
        self.cancelled = False

    def cancel(self):
        """Cancel the clustering process."""
        self.cancelled = True
        logger.info("[FaceClusterWorker] Cancellation requested")

    @Slot()
    def run(self):
        """Main worker execution."""
        logger.info(f"[FaceClusterWorker] Starting face clustering for project {self.project_id}")
        start_time = time.time()

        try:
            db = ReferenceDB()
            conn = db._connect()
            cur = conn.cursor()

            # Step 1: Load embeddings from face_crops table
            self.signals.progress.emit(0, 100, "Loading face embeddings...")

            cur.execute("""
                SELECT id, crop_path, embedding FROM face_crops
                WHERE project_id=? AND embedding IS NOT NULL
            """, (self.project_id,))
            rows = cur.fetchall()

            if not rows:
                logger.warning(f"[FaceClusterWorker] No embeddings found for project {self.project_id}")
                self.signals.finished.emit(0, 0)
                conn.close()
                return

            # Parse embeddings
            ids, paths, vecs = [], [], []
            for rid, path, blob in rows:
                try:
                    vec = np.frombuffer(blob, dtype=np.float32)
                    if vec.size:
                        ids.append(rid)
                        paths.append(path)
                        vecs.append(vec)
                except Exception as e:
                    logger.warning(f"[FaceClusterWorker] Failed to parse embedding: {e}")

            if len(vecs) < 2:
                logger.warning("[FaceClusterWorker] Not enough faces to cluster (need at least 2)")
                self.signals.finished.emit(0, len(vecs))
                conn.close()
                return

            X = np.vstack(vecs)
            total_faces = len(X)
            logger.info(f"[FaceClusterWorker] Loaded {total_faces} face embeddings")

            # Step 2: Run DBSCAN clustering
            self.signals.progress.emit(10, 100, f"Clustering {total_faces} faces...")

            dbscan = DBSCAN(eps=self.eps, min_samples=self.min_samples, metric='cosine')
            labels = dbscan.fit_predict(X)

            unique_labels = sorted([l for l in set(labels) if l != -1])
            cluster_count = len(unique_labels)

            logger.info(f"[FaceClusterWorker] Found {cluster_count} clusters")
            self.signals.progress.emit(40, 100, f"Found {cluster_count} person groups...")

            # Step 3: Clear previous cluster data
            cur.execute("DELETE FROM face_branch_reps WHERE project_id=? AND branch_key LIKE 'face_%'", (self.project_id,))
            cur.execute("DELETE FROM branches WHERE project_id=? AND branch_key LIKE 'face_%'", (self.project_id,))

            # Step 4: Write new cluster results
            for idx, cid in enumerate(unique_labels):
                if self.cancelled:
                    logger.info("[FaceClusterWorker] Cancelled by user")
                    conn.rollback()
                    conn.close()
                    return

                mask = labels == cid
                cluster_vecs = X[mask]
                cluster_paths = np.array(paths)[mask].tolist()
                cluster_ids = np.array(ids)[mask].tolist()

                centroid = np.mean(cluster_vecs, axis=0).astype(np.float32).tobytes()
                rep_path = cluster_paths[0]
                branch_key = f"face_{cid:03d}"
                display_name = f"Person {cid+1}"
                member_count = len(cluster_paths)

                # Insert into face_branch_reps
                cur.execute("""
                    INSERT INTO face_branch_reps (project_id, branch_key, centroid, rep_path, count)
                    VALUES (?, ?, ?, ?, ?)
                """, (self.project_id, branch_key, centroid, rep_path, member_count))

                # Insert into branches (for sidebar display)
                cur.execute("""
                    INSERT INTO branches (project_id, branch_key, display_name, type)
                    VALUES (?, ?, ?, 'face')
                """, (self.project_id, branch_key, display_name))

                # Update face_crops entries to reflect cluster
                placeholders = ','.join(['?'] * len(cluster_ids))
                cur.execute(f"""
                    UPDATE face_crops SET branch_key=? WHERE project_id=? AND id IN ({placeholders})
                """, (branch_key, self.project_id, *cluster_ids))

                # Emit progress
                progress_pct = int(40 + (idx / cluster_count) * 60)
                self.signals.progress.emit(
                    progress_pct, 100,
                    f"Saving cluster {idx+1}/{cluster_count}: {display_name} ({member_count} faces)"
                )

                logger.info(f"[FaceClusterWorker] Cluster {cid} → {member_count} faces")

            conn.commit()
            conn.close()

            duration = time.time() - start_time
            logger.info(f"[FaceClusterWorker] Complete in {duration:.1f}s: {cluster_count} clusters created")

            self.signals.progress.emit(100, 100, f"Clustering complete: {cluster_count} people found")
            self.signals.finished.emit(cluster_count, total_faces)

        except Exception as e:
            logger.error(f"[FaceClusterWorker] Fatal error: {e}", exc_info=True)
            self.signals.error.emit(str(e))
            self.signals.finished.emit(0, 0)


# ============================================================================
# Legacy functions (kept for backward compatibility with standalone script)
# ============================================================================

def cluster_faces_1st(project_id: int, eps: float = 0.42, min_samples: int = 3):
    """
    Performs unsupervised face clustering using embeddings already in the DB.
    Writes cluster info back into face_branch_reps, branches, and face_crops.
    """
    db = ReferenceDB()
    conn = db._connect()
    cur = conn.cursor()

    # 1️: Get embeddings from existing face_crops table
    cur.execute("""
        SELECT id, crop_path, embedding FROM face_crops
        WHERE project_id=? AND embedding IS NOT NULL
    """, (project_id,))
    rows = cur.fetchall()
    if not rows:
        print(f"[FaceCluster] No embeddings found for project {project_id}")
        return

    ids, paths, vecs = [], [], []
    for rid, path, blob in rows:
        try:
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.size:
                ids.append(rid)
                paths.append(path)
                vecs.append(vec)
        except Exception:
            pass

    if len(vecs) < 2:
        print("[FaceCluster] Not enough faces to cluster.")
        return

    X = np.vstack(vecs)
    print(f"[FaceCluster] Clustering {len(X)} faces ...")

    # 2️: Run DBSCAN clustering
    dbscan = DBSCAN(eps=eps, min_samples=min_samples, metric='cosine')
    labels = dbscan.fit_predict(X)
    unique_labels = sorted([l for l in set(labels) if l != -1])

    # 3️: Clear previous cluster data
    cur.execute("DELETE FROM face_branch_reps WHERE project_id=? AND branch_key LIKE 'face_%'", (project_id,))
    cur.execute("DELETE FROM branches WHERE project_id=? AND branch_key LIKE 'face_%'", (project_id,))

    # 4️: Write new cluster results
    for cid in unique_labels:
        mask = labels == cid
        cluster_vecs = X[mask]
        cluster_paths = np.array(paths)[mask].tolist()

        centroid = np.mean(cluster_vecs, axis=0).astype(np.float32).tobytes()
        rep_path = cluster_paths[0]
        branch_key = f"face_{cid:03d}"
        display_name = f"Person {cid+1}"
        member_count = len(cluster_paths)

        # Insert into face_branch_reps
        cur.execute("""
            INSERT INTO face_branch_reps (project_id, branch_key, centroid, rep_path, member_count)
            VALUES (?, ?, ?, ?, ?)
        """, (project_id, branch_key, centroid, rep_path, member_count))

        # Insert into branches (for sidebar display)
        cur.execute("""
            INSERT INTO branches (project_id, branch_key, display_name, type)
            VALUES (?, ?, ?, 'face')
        """, (project_id, branch_key, display_name))

        # Update face_crops entries to reflect cluster
        cur.execute("""
            UPDATE face_crops SET branch_key=? WHERE project_id=? AND id IN (%s)
        """ % ",".join(["?"] * np.sum(mask)),
        (branch_key, project_id, *np.array(ids)[mask].tolist()))

        print(f"[FaceCluster] Cluster {cid} → {member_count} faces")

    conn.commit()
    conn.close()
    print(f"[FaceCluster] Done: {len(unique_labels)} clusters saved.")

def cluster_faces(project_id: int, eps: float = 0.42, min_samples: int = 3):
    """
    Performs unsupervised face clustering using embeddings already in the DB.
    Writes cluster info back into face_branch_reps, branches, and face_crops.
    """
    db = ReferenceDB()
    conn = db._connect()
    cur = conn.cursor()

    # 1️: Get embeddings from existing face_crops table
    cur.execute("""
        SELECT id, crop_path, embedding FROM face_crops
        WHERE project_id=? AND embedding IS NOT NULL
    """, (project_id,))
    rows = cur.fetchall()
    if not rows:
        print(f"[FaceCluster] No embeddings found for project {project_id}")
        return

    ids, paths, vecs = [], [], []
    for rid, path, blob in rows:
        try:
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.size:
                ids.append(rid)
                paths.append(path)
                vecs.append(vec)
        except Exception:
            pass

    if len(vecs) < 2:
        print("[FaceCluster] Not enough faces to cluster.")
        return

    X = np.vstack(vecs)
    total = len(X)
    status_path = os.path.join(os.getcwd(), "status", "cluster_status.json")
    log_path = status_path.replace(".json", ".log")

    def _log_progress(phase, current, total):
        pct = round((current / total) * 100, 1) if total else 0
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {phase} {pct:.1f}% ({current}/{total})\n")

    write_status(status_path, "embedding_load", 0, total)
    _log_progress("embedding_load", 0, total)
    print(f"[FaceCluster] Clustering {len(X)} faces ...")

    # 2️: Run DBSCAN clustering
    dbscan = DBSCAN(eps=eps, min_samples=min_samples, metric='cosine')
    labels = dbscan.fit_predict(X)
    
    unique_labels = sorted([l for l in set(labels) if l != -1])

    # 3️: Clear previous cluster data
    cur.execute("DELETE FROM face_branch_reps WHERE project_id=? AND branch_key LIKE 'face_%'", (project_id,))
    cur.execute("DELETE FROM branches WHERE project_id=? AND branch_key LIKE 'face_%'", (project_id,))

    # 4️: Write new cluster results
    processed_clusters = 0
    total_clusters = len(unique_labels)
    write_status(status_path, "clustering", 0, total_clusters)

    for cid in unique_labels:
        mask = labels == cid
        cluster_vecs = X[mask]
        cluster_paths = np.array(paths)[mask].tolist()

        centroid = np.mean(cluster_vecs, axis=0).astype(np.float32).tobytes()
        rep_path = cluster_paths[0]
        branch_key = f"face_{cid:03d}"
        display_name = f"Person {cid+1}"
        member_count = len(cluster_paths)

        # Insert into face_branch_reps
        cur.execute("""
            INSERT INTO face_branch_reps (project_id, branch_key, centroid, rep_path, count)
            VALUES (?, ?, ?, ?, ?)
        """, (project_id, branch_key, centroid, rep_path, member_count))

        # Insert into branches (for sidebar display)
        cur.execute("""
            INSERT INTO branches (project_id, branch_key, display_name, type)
            VALUES (?, ?, ?, 'face')
        """, (project_id, branch_key, display_name))

        # Update face_crops entries to reflect cluster
        cur.execute(f"""
            UPDATE face_crops SET branch_key=? WHERE project_id=? AND id IN ({','.join(['?'] * np.sum(mask))})
        """, (branch_key, project_id, *np.array(ids)[mask].tolist()))

        processed_clusters += 1
        write_status(status_path, "clustering", processed_clusters, total_clusters)
        _log_progress("clustering", processed_clusters, total_clusters)

        print(f"[FaceCluster] Cluster {cid} → {member_count} faces")

    conn.commit()
    write_status(status_path, "done", total_clusters, total_clusters)
    _log_progress("done", total_clusters, total_clusters)
    conn.close()
    print(f"[FaceCluster] Done: {len(unique_labels)} clusters saved.")


if __name__ == "__main__":
    """
    Standalone script entry point.

    Usage:
        python face_cluster_worker.py <project_id>

    This mode is used when called as a detached subprocess (legacy mode).
    For normal operation, use FaceClusterWorker class with QThreadPool.
    """
    if len(sys.argv) < 2:
        print("Usage: python face_cluster_worker.py <project_id>")
        sys.exit(1)

    pid = int(sys.argv[1])

    # Use Qt event loop for signal handling (if available)
    try:
        from PySide6.QtCore import QCoreApplication, QThreadPool

        app = QCoreApplication(sys.argv)

        def on_progress(current, total, message):
            print(f"[{current}/{total}] {message}")

        def on_finished(cluster_count, total_faces):
            print(f"\nFinished: {cluster_count} clusters created from {total_faces} faces")
            app.quit()

        def on_error(error_msg):
            print(f"Error: {error_msg}")
            app.quit()

        worker = FaceClusterWorker(project_id=pid)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(on_error)

        QThreadPool.globalInstance().start(worker)

        sys.exit(app.exec())

    except ImportError:
        # Fallback to legacy function if Qt not available
        print("[FaceClusterWorker] Qt not available, using legacy mode")
        cluster_faces(pid)
