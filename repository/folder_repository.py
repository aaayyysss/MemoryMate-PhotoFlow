# repository/folder_repository.py
# Version 01.00.00.00 dated 20251102
# Repository for photo_folders table operations

from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository
from logging_config import get_logger

logger = get_logger(__name__)


class FolderRepository(BaseRepository):
    """
    Repository for photo_folders operations.

    Handles folder hierarchy and navigation.
    """

    def _table_name(self) -> str:
        return "photo_folders"

    def get_by_path(self, path: str) -> Optional[Dict[str, Any]]:
        """Get folder by file system path."""
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM photo_folders WHERE path = ?", (path,))
            return cur.fetchone()

    def get_children(self, parent_id: Optional[int]) -> List[Dict[str, Any]]:
        """
        Get all child folders of a parent.

        Args:
            parent_id: Parent folder ID (None for root folders)

        Returns:
            List of child folders
        """
        if parent_id is None:
            where = "parent_id IS NULL"
            params = ()
        else:
            where = "parent_id = ?"
            params = (parent_id,)

        return self.find_all(
            where_clause=where,
            params=params,
            order_by="name ASC"
        )

    def get_all_with_counts(self) -> List[Dict[str, Any]]:
        """
        Get all folders with photo counts.

        Returns:
            List of folders with 'photo_count' field
        """
        sql = """
            SELECT
                f.id,
                f.parent_id,
                f.path,
                f.name,
                COUNT(p.id) as photo_count
            FROM photo_folders f
            LEFT JOIN photo_metadata p ON p.folder_id = f.id
            GROUP BY f.id
            ORDER BY f.parent_id IS NOT NULL, f.parent_id, f.name
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchall()

    def ensure_folder(self, path: str, name: str, parent_id: Optional[int]) -> int:
        """
        Ensure a folder exists in the database.

        Args:
            path: Full file system path
            name: Folder display name
            parent_id: Parent folder ID (None for root)

        Returns:
            Folder ID
        """
        # Check if exists
        existing = self.get_by_path(path)
        if existing:
            return existing['id']

        # Insert new folder
        sql = """
            INSERT INTO photo_folders (path, name, parent_id)
            VALUES (?, ?, ?)
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (path, name, parent_id))
            conn.commit()
            folder_id = cur.lastrowid

        self.logger.debug(f"Created folder: {path} (id={folder_id})")
        return folder_id

    def get_folder_tree(self) -> List[Dict[str, Any]]:
        """
        Get folder hierarchy as a flat list with depth indicators.

        Returns:
            List of folders with computed depth
        """
        sql = """
            WITH RECURSIVE folder_tree AS (
                -- Root folders
                SELECT
                    id, parent_id, path, name,
                    0 as depth,
                    name as full_path
                FROM photo_folders
                WHERE parent_id IS NULL

                UNION ALL

                -- Child folders
                SELECT
                    f.id, f.parent_id, f.path, f.name,
                    ft.depth + 1,
                    ft.full_path || '/' || f.name
                FROM photo_folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
            )
            SELECT * FROM folder_tree
            ORDER BY full_path
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                return cur.fetchall()
            except Exception as e:
                # Fallback if recursive CTE not supported
                self.logger.warning(f"Recursive query failed: {e}, using simple query")
                return self.find_all(order_by="name ASC")

    def update_photo_count(self, folder_id: int, count: int):
        """
        Update the photo count for a folder.

        Args:
            folder_id: Folder ID
            count: Number of photos
        """
        sql = "UPDATE photo_folders SET photo_count = ? WHERE id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (count, folder_id))
            conn.commit()

        self.logger.debug(f"Updated folder {folder_id} photo count to {count}")

    def get_recursive_photo_count(self, folder_id: int) -> int:
        """
        Get total photo count including all subfolders.

        Args:
            folder_id: Folder ID

        Returns:
            Total photo count recursively
        """
        sql = """
            WITH RECURSIVE folder_tree AS (
                SELECT id FROM photo_folders WHERE id = ?
                UNION ALL
                SELECT f.id
                FROM photo_folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
            )
            SELECT COUNT(DISTINCT p.id) as count
            FROM photo_metadata p
            WHERE p.folder_id IN (SELECT id FROM folder_tree)
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (folder_id,))
                result = cur.fetchone()
                return result['count'] if result else 0
            except Exception as e:
                # Fallback to non-recursive count
                self.logger.warning(f"Recursive count failed: {e}, using simple count")
                from .photo_repository import PhotoRepository
                photo_repo = PhotoRepository(self.db_conn)
                return photo_repo.count_by_folder(folder_id)

    def get_all_folders(self) -> List[Dict[str, Any]]:
        """
        Get all folders ordered by path.

        Returns:
            List of all folders
        """
        return self.find_all(order_by="path ASC")

    def delete_folder(self, folder_id: int) -> bool:
        """
        Delete a folder (only if it has no photos).

        Args:
            folder_id: Folder ID

        Returns:
            True if deleted, False otherwise
        """
        # Check if folder has photos
        from .photo_repository import PhotoRepository
        photo_repo = PhotoRepository(self.db_conn)
        count = photo_repo.count_by_folder(folder_id)

        if count > 0:
            self.logger.warning(f"Cannot delete folder {folder_id}: has {count} photos")
            return False

        # Check if folder has children
        children = self.get_children(folder_id)
        if children:
            self.logger.warning(f"Cannot delete folder {folder_id}: has {len(children)} child folders")
            return False

        # Delete folder
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM photo_folders WHERE id = ?", (folder_id,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted folder {folder_id}")

        return deleted
