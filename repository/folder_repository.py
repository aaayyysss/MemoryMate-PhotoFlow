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
