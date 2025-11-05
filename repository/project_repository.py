# repository/project_repository.py
# Version 01.00.00.00 dated 20251102
# Repository for projects and branches

from typing import Optional, List, Dict, Any
from datetime import datetime
from .base_repository import BaseRepository
from logging_config import get_logger

logger = get_logger(__name__)


class ProjectRepository(BaseRepository):
    """
    Repository for projects table operations.

    Handles project CRUD and related branch operations.
    """

    def _table_name(self) -> str:
        return "projects"

    def create(self, name: str, folder: str, mode: str) -> int:
        """
        Create a new project.

        Args:
            name: Project name
            folder: Root folder path
            mode: Project mode (date, faces, etc.)

        Returns:
            New project ID
        """
        sql = """
            INSERT INTO projects (name, folder, mode, created_at)
            VALUES (?, ?, ?, ?)
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (name, folder, mode, datetime.now().isoformat()))
            conn.commit()
            project_id = cur.lastrowid

        self.logger.info(f"Created project: {name} (id={project_id})")
        return project_id

    def get_all_with_details(self) -> List[Dict[str, Any]]:
        """
        Get all projects with branch and image counts.

        Returns:
            List of projects with additional metadata
        """
        sql = """
            SELECT
                p.id,
                p.name,
                p.folder,
                p.mode,
                p.created_at,
                COUNT(DISTINCT b.id) as branch_count,
                COUNT(DISTINCT pi.id) as image_count
            FROM projects p
            LEFT JOIN branches b ON b.project_id = p.id
            LEFT JOIN project_images pi ON pi.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchall()

    def get_branches(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all branches for a project.

        Args:
            project_id: Project ID

        Returns:
            List of branches
        """
        sql = """
            SELECT branch_key, display_name
            FROM branches
            WHERE project_id = ?
            ORDER BY branch_key ASC
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id,))
            return cur.fetchall()

    def ensure_branch(self, project_id: int, branch_key: str, display_name: str) -> int:
        """
        Ensure a branch exists for a project.

        Args:
            project_id: Project ID
            branch_key: Unique branch identifier
            display_name: Human-readable name

        Returns:
            Branch ID
        """
        # Check if exists
        sql_check = """
            SELECT id FROM branches
            WHERE project_id = ? AND branch_key = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_check, (project_id, branch_key))
            existing = cur.fetchone()

            if existing:
                return existing['id']

            # Create new
            sql_insert = """
                INSERT INTO branches (project_id, branch_key, display_name)
                VALUES (?, ?, ?)
            """

            cur.execute(sql_insert, (project_id, branch_key, display_name))
            conn.commit()
            branch_id = cur.lastrowid

        self.logger.debug(f"Created branch: {branch_key} for project {project_id}")
        return branch_id

    def get_branch_by_key(self, project_id: int, branch_key: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific branch by key.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            Branch dict or None
        """
        sql = """
            SELECT * FROM branches
            WHERE project_id = ? AND branch_key = ?
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, branch_key))
            return cur.fetchone()

    def get_branch_image_count(self, project_id: int, branch_key: str) -> int:
        """
        Get number of images in a branch.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            Number of images
        """
        sql = """
            SELECT COUNT(*) as count
            FROM project_images pi
            JOIN branches b ON b.id = pi.branch_id
            WHERE b.project_id = ? AND b.branch_key = ?
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, branch_key))
            result = cur.fetchone()
            return result['count'] if result else 0

    def add_image_to_branch(self, branch_id: int, photo_id: int) -> bool:
        """
        Add an image to a branch.

        Args:
            branch_id: Branch ID
            photo_id: Photo ID

        Returns:
            True if added, False if already exists
        """
        sql = """
            INSERT OR IGNORE INTO project_images (project_id, branch_id, photo_id)
            SELECT b.project_id, ?, ?
            FROM branches b
            WHERE b.id = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (branch_id, photo_id, branch_id))
            conn.commit()
            added = cur.rowcount > 0

        if added:
            self.logger.debug(f"Added photo {photo_id} to branch {branch_id}")

        return added

    def bulk_add_images_to_branch(self, branch_id: int, photo_ids: List[int]) -> int:
        """
        Add multiple images to a branch.

        Args:
            branch_id: Branch ID
            photo_ids: List of photo IDs

        Returns:
            Number of images added
        """
        if not photo_ids:
            return 0

        # First get the project_id for this branch
        sql_get_project = "SELECT project_id FROM branches WHERE id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_get_project, (branch_id,))
            result = cur.fetchone()

            if not result:
                self.logger.warning(f"Branch {branch_id} not found")
                return 0

            project_id = result['project_id']

            # Bulk insert
            sql_insert = """
                INSERT OR IGNORE INTO project_images (project_id, branch_id, photo_id)
                VALUES (?, ?, ?)
            """

            rows = [(project_id, branch_id, photo_id) for photo_id in photo_ids]
            cur.executemany(sql_insert, rows)
            conn.commit()
            added = cur.rowcount

        self.logger.info(f"Added {added} images to branch {branch_id}")
        return added

    def remove_image_from_branch(self, branch_id: int, photo_id: int) -> bool:
        """
        Remove an image from a branch.

        Args:
            branch_id: Branch ID
            photo_id: Photo ID

        Returns:
            True if removed, False if not found
        """
        sql = "DELETE FROM project_images WHERE branch_id = ? AND photo_id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (branch_id, photo_id))
            conn.commit()
            removed = cur.rowcount > 0

        if removed:
            self.logger.debug(f"Removed photo {photo_id} from branch {branch_id}")

        return removed

    def delete_branch(self, branch_id: int) -> bool:
        """
        Delete a branch and all its image associations.

        Args:
            branch_id: Branch ID

        Returns:
            True if deleted
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Delete image associations first
            cur.execute("DELETE FROM project_images WHERE branch_id = ?", (branch_id,))

            # Delete branch
            cur.execute("DELETE FROM branches WHERE id = ?", (branch_id,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted branch {branch_id}")

        return deleted
