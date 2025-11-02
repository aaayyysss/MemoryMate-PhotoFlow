# face_cluster_worker.py
# Version 01.01.01.01  (Phase 7.1 – People / Face Albums)
# Reuses face_branch_reps + face_crops for clustering
# ------------------------------------------------------

import os
import sys
import sqlite3
import numpy as np
from sklearn.cluster import DBSCAN
from reference_db import ReferenceDB

def cluster_faces(project_id: int, eps: float = 0.42, min_samples: int = 3):
    """
    Performs unsupervised face clustering using embeddings already in the DB.
    Writes cluster info back into face_branch_reps, branches, and face_crops.
    """
    db = ReferenceDB()
    conn = db._connect()
    cur = conn.cursor()

    # 1️⃣ Get embeddings from existing face_crops table
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

    # 2️⃣ Run DBSCAN clustering
    dbscan = DBSCAN(eps=eps, min_samples=min_samples, metric='cosine')
    labels = dbscan.fit_predict(X)
    unique_labels = sorted([l for l in set(labels) if l != -1])

    # 3️⃣ Clear previous cluster data
    cur.execute("DELETE FROM face_branch_reps WHERE project_id=? AND branch_key LIKE 'face_%'", (project_id,))
    cur.execute("DELETE FROM branches WHERE project_id=? AND key LIKE 'face_%'", (project_id,))

    # 4️⃣ Write new cluster results
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
            INSERT INTO branches (project_id, key, display_name, type)
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python face_cluster_worker.py <project_id>")
        sys.exit(1)
    pid = int(sys.argv[1])
    cluster_faces(pid)
