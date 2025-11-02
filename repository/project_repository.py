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
