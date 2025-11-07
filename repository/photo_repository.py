# repository/photo_repository.py
# Version 01.00.00.00 dated 20251102
# Repository for photo_metadata table operations

from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository, DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


class PhotoRepository(BaseRepository):
    """
    Repository for photo_metadata operations.

    Handles all database operations related to photo metadata:
    - CRUD operations
    - Searching and filtering
    - Metadata updates
    - Bulk operations
    """

    def _table_name(self) -> str:
        return "photo_metadata"

    def get_by_path(self, path: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get photo metadata by file path and project.

        Args:
            path: Full file path
            project_id: Project ID

        Returns:
            Photo metadata dict or None
        """
        # Normalize path for consistent lookups (handles Windows backslash/forward slash)
        normalized_path = self._normalize_path(path)

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM photo_metadata WHERE path = ? AND project_id = ?",
                (normalized_path, project_id)
            )
            return cur.fetchone()

    def _normalize_path(self, path: str) -> str:
        """
        Normalize file path for consistent database storage.

        On Windows, converts backslashes to forward slashes and normalizes case.
        This prevents duplicates like 'C:\\path\\photo.jpg' vs 'C:/path/photo.jpg'

        Args:
            path: File path to normalize

        Returns:
            Normalized path string
        """
        import os
        # Normalize path components (resolve .., ., etc)
        normalized = os.path.normpath(path)
        # Convert backslashes to forward slashes for consistent storage
        # SQLite stores paths as strings, so C:\path != C:/path
        normalized = normalized.replace('\\', '/')
        return normalized

    def get_by_folder(self, folder_id: int, project_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all photos in a folder within a project.

        Args:
            folder_id: Folder ID
            project_id: Project ID
            limit: Optional maximum number of results

        Returns:
            List of photo metadata dicts
        """
        return self.find_all(
            where_clause="folder_id = ? AND project_id = ?",
            params=(folder_id, project_id),
            order_by="modified DESC",
            limit=limit
        )

    def get_by_date_range(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Get photos taken within a date range.

        Args:
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)

        Returns:
            List of photo metadata dicts
        """
        return self.find_all(
            where_clause="date_taken >= ? AND date_taken <= ?",
            params=(start_date, end_date),
            order_by="date_taken ASC"
        )

    def upsert(self,
               path: str,
               folder_id: int,
               project_id: int,
               size_kb: Optional[float] = None,
               modified: Optional[str] = None,
               width: Optional[int] = None,
               height: Optional[int] = None,
               date_taken: Optional[str] = None,
               tags: Optional[str] = None) -> int:
        """
        Insert or update photo metadata for a project.

        Args:
            path: Full file path
            folder_id: Folder ID
            project_id: Project ID
            size_kb: File size in KB
            modified: Last modified timestamp
            width: Image width in pixels
            height: Image height in pixels
            date_taken: EXIF date taken
            tags: Comma-separated tags

        Returns:
            Photo ID (newly inserted or existing)
        """
        import time

        # Normalize path for consistent storage (prevents duplicates on Windows)
        normalized_path = self._normalize_path(path)

        now = time.strftime("%Y-%m-%d %H:%M:%S")

        sql = """
            INSERT INTO photo_metadata
                (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, project_id) DO UPDATE SET
                folder_id = excluded.folder_id,
                size_kb = excluded.size_kb,
                modified = excluded.modified,
                width = excluded.width,
                height = excluded.height,
                date_taken = excluded.date_taken,
                tags = excluded.tags,
                updated_at = excluded.updated_at
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (normalized_path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, now))
            conn.commit()

            # Get the ID of the inserted/updated row
            cur.execute("SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?", (normalized_path, project_id))
            result = cur.fetchone()
            photo_id = result['id'] if result else None

        self.logger.debug(f"Upserted photo: {normalized_path} (id={photo_id}, project={project_id})")
        return photo_id

    def bulk_upsert(self, rows: List[tuple], project_id: int) -> int:
        """
        Bulk insert or update multiple photos for a project.

        Args:
            rows: List of tuples: (path, folder_id, size_kb, modified, width, height, date_taken, tags)
            project_id: Project ID

        Returns:
            Number of rows affected
        """
        if not rows:
            return 0

        import time
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # Normalize paths and add project_id + updated_at timestamp to each row
        rows_normalized = []
        for row in rows:
            # Unpack: (path, folder_id, size_kb, modified, width, height, date_taken, tags)
            path = row[0]
            normalized_path = self._normalize_path(path)
            # Rebuild tuple with normalized path and project_id
            # New order: (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, updated_at)
            normalized_row = (normalized_path, row[1], project_id) + row[2:] + (now,)
            rows_normalized.append(normalized_row)

        rows_with_timestamp = rows_normalized

        sql = """
            INSERT INTO photo_metadata
                (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, project_id) DO UPDATE SET
                folder_id = excluded.folder_id,
                size_kb = excluded.size_kb,
                modified = excluded.modified,
                width = excluded.width,
                height = excluded.height,
                date_taken = excluded.date_taken,
                tags = excluded.tags,
                updated_at = excluded.updated_at
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.executemany(sql, rows_with_timestamp)
            conn.commit()
            affected = cur.rowcount

        self.logger.info(f"Bulk upserted {affected} photos for project {project_id}")
        return affected

    def update_metadata_status(self, photo_id: int, status: str, fail_count: int = 0):
        """
        Update metadata extraction status.

        Args:
            photo_id: Photo ID
            status: Status string (pending, success, failed)
            fail_count: Number of failed attempts
        """
        sql = """
            UPDATE photo_metadata
            SET metadata_status = ?, metadata_fail_count = ?
            WHERE id = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (status, fail_count, photo_id))
            conn.commit()

        self.logger.debug(f"Updated metadata status for photo {photo_id}: {status}")

    def get_missing_metadata(self, max_failures: int = 3, limit: Optional[int] = None) -> List[str]:
        """
        Get photos that need metadata extraction.

        Args:
            max_failures: Maximum allowed failure count
            limit: Optional maximum number of results

        Returns:
            List of file paths needing metadata
        """
        sql = """
            SELECT path FROM photo_metadata
            WHERE metadata_status = 'pending'
               OR (metadata_status = 'failed' AND metadata_fail_count < ?)
            ORDER BY id ASC
        """

        if limit:
            sql += f" LIMIT {int(limit)}"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (max_failures,))
            return [row['path'] for row in cur.fetchall()]

    def count_by_folder(self, folder_id: int, project_id: int) -> int:
        """
        Count photos in a specific folder within a project.

        Args:
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            Number of photos
        """
        return self.count(where_clause="folder_id = ? AND project_id = ?", params=(folder_id, project_id))

    def search(self,
               query: str,
               limit: int = 100) -> List[Dict[str, Any]]:
        """
        Search photos by path or tags.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching photos
        """
        pattern = f"%{query}%"

        return self.find_all(
            where_clause="path LIKE ? OR tags LIKE ?",
            params=(pattern, pattern),
            order_by="modified DESC",
            limit=limit
        )

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get database statistics.

        Returns:
            Dict with counts and aggregates
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()

            # Total count
            cur.execute("SELECT COUNT(*) as total FROM photo_metadata")
            total = cur.fetchone()['total']

            # Count by status
            cur.execute("""
                SELECT metadata_status, COUNT(*) as count
                FROM photo_metadata
                GROUP BY metadata_status
            """)
            by_status = {row['metadata_status']: row['count'] for row in cur.fetchall()}

            # Total size
            cur.execute("SELECT SUM(size_kb) as total_size FROM photo_metadata")
            total_size_kb = cur.fetchone()['total_size'] or 0

            return {
                "total_photos": total,
                "by_status": by_status,
                "total_size_mb": round(total_size_kb / 1024, 2)
            }

    def delete_by_path(self, path: str) -> bool:
        """
        Delete a photo by file path.

        Args:
            path: Full file path

        Returns:
            True if deleted, False if not found
        """
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM photo_metadata WHERE path = ?", (path,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted photo: {path}")
        else:
            self.logger.warning(f"Photo not found for deletion: {path}")

        return deleted

    def delete_by_paths(self, paths: List[str]) -> int:
        """
        Delete multiple photos by file paths.

        Args:
            paths: List of file paths

        Returns:
            Number of photos deleted
        """
        if not paths:
            return 0

        placeholders = ','.join('?' * len(paths))
        sql = f"DELETE FROM photo_metadata WHERE path IN ({placeholders})"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, paths)
            conn.commit()
            deleted = cur.rowcount

        self.logger.info(f"Bulk deleted {deleted} photos")
        return deleted

    def delete_by_folder(self, folder_id: int) -> int:
        """
        Delete all photos in a folder.

        Args:
            folder_id: Folder ID

        Returns:
            Number of photos deleted
        """
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM photo_metadata WHERE folder_id = ?", (folder_id,))
            conn.commit()
            deleted = cur.rowcount

        self.logger.info(f"Deleted {deleted} photos from folder {folder_id}")
        return deleted

    def cleanup_duplicate_paths(self) -> int:
        """
        Clean up duplicate photo entries caused by path format differences.

        Removes duplicates where paths differ only in slash direction (e.g.,
        'C:\\path\\photo.jpg' vs 'C:/path/photo.jpg'), keeping the entry
        with the lowest ID (oldest).

        Returns:
            Number of duplicate entries removed
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Find all photo paths
            cur.execute("SELECT id, path FROM photo_metadata ORDER BY id")
            all_photos = cur.fetchall()

            # Build map of normalized_path -> list of (id, original_path)
            normalized_map = {}
            for row in all_photos:
                photo_id = row['id']
                path = row['path']
                normalized = self._normalize_path(path)

                if normalized not in normalized_map:
                    normalized_map[normalized] = []
                normalized_map[normalized].append((photo_id, path))

            # Find duplicates and collect IDs to delete
            ids_to_delete = []
            for normalized, entries in normalized_map.items():
                if len(entries) > 1:
                    # Sort by ID (keep oldest), delete the rest
                    entries_sorted = sorted(entries, key=lambda x: x[0])
                    keep_id, keep_path = entries_sorted[0]

                    # Mark duplicates for deletion
                    for dup_id, dup_path in entries_sorted[1:]:
                        ids_to_delete.append(dup_id)
                        self.logger.debug(f"Duplicate found: keeping ID={keep_id} '{keep_path}', removing ID={dup_id} '{dup_path}'")

            # Delete duplicates
            if ids_to_delete:
                placeholders = ','.join('?' * len(ids_to_delete))
                sql = f"DELETE FROM photo_metadata WHERE id IN ({placeholders})"
                cur.execute(sql, ids_to_delete)
                conn.commit()

            deleted_count = len(ids_to_delete)
            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} duplicate photo entries")
            else:
                self.logger.info("No duplicate photo entries found")

            return deleted_count
