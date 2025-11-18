# repository/migrations.py
# Version 2.0.1 dated 20251103
# Database migration system for schema upgrades
# FIX: Check if DB file exists before opening in read-only mode (prevents error on fresh DB)
#
# This module handles schema migrations from legacy databases to current version.
# It provides safe, incremental schema upgrades with full tracking and validation.

"""
Database migration system for MemoryMate-PhotoFlow.

This module provides:
- Migration definitions for each schema version
- Migration detection (current version vs target version)
- Safe migration application with transaction support
- Migration history tracking
- Pre-flight checks and validation

Usage:
    from repository.migrations import MigrationManager

    manager = MigrationManager(db_connection)

    # Check if migrations are needed
    if manager.needs_migration():
        print(f"Migrations needed: {manager.get_pending_migrations()}")

        # Apply all pending migrations
        results = manager.apply_all_migrations()

        # Check results
        for result in results:
            print(f"Applied: {result['version']} - {result['status']}")
"""

import sqlite3
from typing import List, Dict, Any, Optional, Tuple
from logging_config import get_logger
from datetime import datetime

logger = get_logger(__name__)


# =============================================================================
# MIGRATION DEFINITIONS
# =============================================================================

class Migration:
    """
    Base class for database migrations.

    Each migration represents an atomic schema change with:
    - Version number (semantic versioning)
    - Description of changes
    - SQL to apply the migration
    - Optional rollback SQL
    - Pre-flight checks
    """

    def __init__(self, version: str, description: str, sql: str, rollback_sql: str = ""):
        self.version = version
        self.description = description
        self.sql = sql
        self.rollback_sql = rollback_sql

    def __repr__(self):
        return f"Migration(version={self.version}, description={self.description})"


# Migration from legacy (no schema_version table) to v1.5.0 (add created_* columns)
MIGRATION_1_5_0 = Migration(
    version="1.5.0",
    description="Add created_ts, created_date, created_year columns and indexes",
    sql="""
-- Check if columns already exist (idempotent)
-- SQLite doesn't have IF NOT EXISTS for ALTER TABLE, so we'll handle in code

-- Add created_ts column
-- ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER;

-- Add created_date column
-- ALTER TABLE photo_metadata ADD COLUMN created_date TEXT;

-- Add created_year column
-- ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER;

-- Create indexes for date-based queries
CREATE INDEX IF NOT EXISTS idx_photo_created_year ON photo_metadata(created_year);
CREATE INDEX IF NOT EXISTS idx_photo_created_date ON photo_metadata(created_date);
CREATE INDEX IF NOT EXISTS idx_photo_created_ts ON photo_metadata(created_ts);
""",
    rollback_sql="""
-- Cannot drop columns in SQLite without recreating table
-- This is intentionally left empty as column drops are complex
-- Manual rollback required if needed
"""
)

# Migration to v2.0.0 (full repository layer schema)
MIGRATION_2_0_0 = Migration(
    version="2.0.0",
    description="Repository layer schema with schema_version tracking",
    sql="""
-- This migration brings legacy databases up to v2.0.0 standard

-- 1. Create schema_version table if it doesn't exist
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- 2. Ensure all tables exist (idempotent)
-- Reference images for face recognition
CREATE TABLE IF NOT EXISTS reference_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    matched_label TEXT,
    confidence REAL,
    match_mode TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reference_labels (
    label TEXT PRIMARY KEY,
    folder_path TEXT NOT NULL,
    threshold REAL DEFAULT 0.3
);

-- Projects and branches
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key)
);

CREATE TABLE IF NOT EXISTS project_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT,
    image_path TEXT NOT NULL,
    label TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Face recognition tables
CREATE TABLE IF NOT EXISTS face_crops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    image_path TEXT NOT NULL,
    crop_path TEXT NOT NULL,
    is_representative INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key, crop_path)
);

CREATE TABLE IF NOT EXISTS face_branch_reps (
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    label TEXT,
    count INTEGER DEFAULT 0,
    centroid BLOB,
    rep_path TEXT,
    rep_thumb_png BLOB,
    PRIMARY KEY (project_id, branch_key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS face_merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    target_branch TEXT NOT NULL,
    source_branches TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS export_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    branch_key TEXT,
    photo_count INTEGER,
    source_paths TEXT,
    dest_paths TEXT,
    dest_folder TEXT,
    timestamp TEXT
);

-- Tags (normalized structure)
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (photo_id, tag_id),
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- 3. Create all indexes (idempotent)
CREATE INDEX IF NOT EXISTS idx_face_crops_proj ON face_crops(project_id);
CREATE INDEX IF NOT EXISTS idx_face_crops_proj_branch ON face_crops(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_face_crops_proj_rep ON face_crops(project_id, is_representative);
CREATE INDEX IF NOT EXISTS idx_fbreps_proj ON face_branch_reps(project_id);
CREATE INDEX IF NOT EXISTS idx_fbreps_proj_branch ON face_branch_reps(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_face_merge_history_proj ON face_merge_history(project_id);
CREATE INDEX IF NOT EXISTS idx_branches_project ON branches(project_id);
CREATE INDEX IF NOT EXISTS idx_branches_key ON branches(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_projimgs_project ON project_images(project_id);
CREATE INDEX IF NOT EXISTS idx_projimgs_branch ON project_images(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_projimgs_path ON project_images(image_path);
CREATE INDEX IF NOT EXISTS idx_meta_date ON photo_metadata(date_taken);
CREATE INDEX IF NOT EXISTS idx_meta_modified ON photo_metadata(modified);
CREATE INDEX IF NOT EXISTS idx_meta_updated ON photo_metadata(updated_at);
CREATE INDEX IF NOT EXISTS idx_meta_folder ON photo_metadata(folder_id);
CREATE INDEX IF NOT EXISTS idx_meta_status ON photo_metadata(metadata_status);
CREATE INDEX IF NOT EXISTS idx_photo_created_year ON photo_metadata(created_year);
CREATE INDEX IF NOT EXISTS idx_photo_created_date ON photo_metadata(created_date);
CREATE INDEX IF NOT EXISTS idx_photo_created_ts ON photo_metadata(created_ts);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);

-- 4. Record migration
INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('2.0.0', 'Repository layer schema with full migration', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v3.0.0 (add project_id for project isolation)
MIGRATION_3_0_0 = Migration(
    version="3.0.0",
    description="Add project_id to photo_folders and photo_metadata for clean project isolation",
    sql="""
-- This migration adds project_id columns to photo_folders and photo_metadata
-- for proper project isolation at the schema level

-- 1. Add project_id columns with default value of 1 (first project)
--    Note: ALTER TABLE will be handled in code (see _add_project_id_columns_if_missing)

-- 2. Create indexes for project_id (if they don't exist yet)
CREATE INDEX IF NOT EXISTS idx_photo_folders_project ON photo_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project ON photo_metadata(project_id);

-- 3. Ensure default project exists
INSERT OR IGNORE INTO projects (id, name, folder, mode, created_at)
VALUES (1, 'Default Project', '', 'date', CURRENT_TIMESTAMP);

-- 4. Record migration
INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('3.0.0', 'Added project_id to photo_folders and photo_metadata for clean project isolation', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v4.0.0 (add file_hash for duplicate detection during device imports)
MIGRATION_4_0_0 = Migration(
    version="4.0.0",
    description="Add file_hash column for duplicate detection during device imports",
    sql="""
-- This migration adds file_hash column to photo_metadata for duplicate detection
-- during mobile device imports (prevents importing same photo twice)

-- Note: ALTER TABLE will be handled in code (see _add_file_hash_column_if_missing)

-- Create index for faster duplicate detection
CREATE INDEX IF NOT EXISTS idx_photo_metadata_hash ON photo_metadata(file_hash);

-- Record migration
INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('4.0.0', 'Added file_hash column for duplicate detection during device imports', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Ordered list of all migrations
ALL_MIGRATIONS = [
    MIGRATION_1_5_0,
    MIGRATION_2_0_0,
    MIGRATION_3_0_0,
    MIGRATION_4_0_0,
]


# =============================================================================
# MIGRATION MANAGER
# =============================================================================

class MigrationManager:
    """
    Manages database schema migrations.

    Responsibilities:
    - Detect current schema version
    - Identify pending migrations
    - Apply migrations safely with transactions
    - Track migration history
    - Validate schema after migrations
    """

    def __init__(self, db_connection):
        """
        Initialize migration manager.

        Args:
            db_connection: DatabaseConnection instance
        """
        from .base_repository import DatabaseConnection
        self.db_connection: DatabaseConnection = db_connection
        self.logger = get_logger(self.__class__.__name__)

    def get_current_version(self) -> str:
        """
        Get the current schema version from the database.

        Returns:
            str: Current version (e.g., "2.0.0") or "0.0.0" if no schema exists
        """
        import os
        db_path = self.db_connection._db_path

        # CRITICAL FIX: If database file doesn't exist, return 0.0.0 immediately
        # Cannot open non-existent file in read-only mode - SQLite will fail
        if not os.path.exists(db_path):
            return "0.0.0"

        try:
            with self.db_connection.get_connection(read_only=True) as conn:
                cur = conn.cursor()

                # Check if schema_version table exists
                cur.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='schema_version'
                """)

                schema_version_result = cur.fetchone()

                if not schema_version_result:
                    # No schema_version table - this is a legacy database
                    # Check if photo_metadata exists to distinguish v0 from v1
                    cur.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='photo_metadata'
                    """)

                    photo_metadata_result = cur.fetchone()

                    if photo_metadata_result:
                        # Has tables but no versioning - legacy v1.0
                        return "1.0.0"
                    else:
                        # No tables at all - fresh database
                        return "0.0.0"

                # Get latest version from schema_version table
                cur.execute("""
                    SELECT version FROM schema_version
                    ORDER BY applied_at DESC
                    LIMIT 1
                """)

                result = cur.fetchone()

                version = result['version'] if result else "0.0.0"
                return version

        except Exception as e:
            self.logger.error(f"Error getting current version: {e}", exc_info=True)
            return "0.0.0"

    def get_target_version(self) -> str:
        """
        Get the target schema version (latest available).

        Returns:
            str: Target version
        """
        from .schema import get_schema_version
        return get_schema_version()

    def needs_migration(self) -> bool:
        """
        Check if any migrations need to be applied.

        Returns:
            bool: True if migrations are pending
        """
        current = self.get_current_version()
        target = self.get_target_version()

        return self._compare_versions(current, target) < 0

    def get_pending_migrations(self) -> List[Migration]:
        """
        Get list of pending migrations that need to be applied.

        Returns:
            List[Migration]: Migrations to apply, in order
        """
        current = self.get_current_version()
        pending = []

        for migration in ALL_MIGRATIONS:
            if self._compare_versions(current, migration.version) < 0:
                pending.append(migration)

        return pending

    def apply_migration(self, migration: Migration) -> Dict[str, Any]:
        """
        Apply a single migration.

        Args:
            migration: Migration to apply

        Returns:
            dict: Result with status, version, duration, etc.
        """
        start_time = datetime.now()

        try:
            self.logger.info(f"Applying migration {migration.version}: {migration.description}")

            with self.db_connection.get_connection() as conn:
                # First, add any missing columns (ALTER TABLE can't be in executescript)
                if migration.version == "1.5.0":
                    self._add_created_columns_if_missing(conn)
                elif migration.version == "2.0.0":
                    self._add_created_columns_if_missing(conn)
                    self._add_metadata_columns_if_missing(conn)
                elif migration.version == "3.0.0":
                    self._add_project_id_columns_if_missing(conn)
                elif migration.version == "4.0.0":
                    self._add_file_hash_column_if_missing(conn)

                # Execute migration SQL
                conn.executescript(migration.sql)
                conn.commit()

            duration = (datetime.now() - start_time).total_seconds()

            self.logger.info(f"✓ Migration {migration.version} applied successfully ({duration:.2f}s)")

            return {
                "status": "success",
                "version": migration.version,
                "description": migration.description,
                "duration_seconds": duration,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            self.logger.error(f"✗ Migration {migration.version} failed: {e}", exc_info=True)

            return {
                "status": "failed",
                "version": migration.version,
                "description": migration.description,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": datetime.now().isoformat()
            }

    def apply_all_migrations(self) -> List[Dict[str, Any]]:
        """
        Apply all pending migrations in order.

        Returns:
            List[dict]: Results for each migration
        """
        pending = self.get_pending_migrations()

        if not pending:
            self.logger.info("No pending migrations")
            return []

        self.logger.info(f"Applying {len(pending)} pending migrations")
        results = []

        for migration in pending:
            result = self.apply_migration(migration)
            results.append(result)

            # Stop if migration failed
            if result["status"] == "failed":
                self.logger.error(f"Migration failed, stopping at {migration.version}")
                break

        return results

    def get_migration_history(self) -> List[Dict[str, Any]]:
        """
        Get history of applied migrations.

        Returns:
            List[dict]: Migration history records
        """
        try:
            with self.db_connection.get_connection(read_only=True) as conn:
                cur = conn.cursor()

                # Check if schema_version table exists
                cur.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='schema_version'
                """)

                if not cur.fetchone():
                    return []

                cur.execute("""
                    SELECT version, description, applied_at
                    FROM schema_version
                    ORDER BY applied_at ASC
                """)

                return [
                    {
                        "version": row['version'],
                        "description": row['description'],
                        "applied_at": row['applied_at']
                    }
                    for row in cur.fetchall()
                ]

        except Exception as e:
            self.logger.error(f"Error getting migration history: {e}", exc_info=True)
            return []

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        Compare two semantic version strings.

        Args:
            v1: First version (e.g., "1.5.0")
            v2: Second version (e.g., "2.0.0")

        Returns:
            int: -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
        """
        def parse_version(v: str) -> Tuple[int, int, int]:
            parts = v.split(".")
            return (
                int(parts[0]) if len(parts) > 0 else 0,
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0
            )

        v1_parts = parse_version(v1)
        v2_parts = parse_version(v2)

        if v1_parts < v2_parts:
            return -1
        elif v1_parts > v2_parts:
            return 1
        else:
            return 0

    def _add_created_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add created_ts, created_date, created_year columns if they don't exist.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        columns = {row['name'] for row in cur.fetchall()}

        if 'created_ts' not in columns:
            self.logger.info("Adding column: created_ts")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER")

        if 'created_date' not in columns:
            self.logger.info("Adding column: created_date")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_date TEXT")

        if 'created_year' not in columns:
            self.logger.info("Adding column: created_year")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER")

        conn.commit()

    def _add_metadata_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add metadata_status and metadata_fail_count columns if they don't exist.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        columns = {row['name'] for row in cur.fetchall()}

        if 'metadata_status' not in columns:
            self.logger.info("Adding column: metadata_status")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN metadata_status TEXT DEFAULT 'pending'")

        if 'metadata_fail_count' not in columns:
            self.logger.info("Adding column: metadata_fail_count")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN metadata_fail_count INTEGER DEFAULT 0")

        conn.commit()

    def _add_project_id_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add project_id columns to photo_folders and photo_metadata if they don't exist.

        This is the core of the v3.0.0 migration - adds project ownership to photos and folders.
        Existing rows will default to project_id=1 (default project).

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_folders for project_id column
        cur.execute("PRAGMA table_info(photo_folders)")
        folder_columns = {row['name'] for row in cur.fetchall()}

        if 'project_id' not in folder_columns:
            self.logger.info("Adding column photo_folders.project_id (default=1)")
            cur.execute("""
                ALTER TABLE photo_folders
                ADD COLUMN project_id INTEGER NOT NULL DEFAULT 1
            """)
            # Add foreign key constraint note: SQLite doesn't enforce FK on ALTER,
            # but new schema creation will have proper FK

        # Check photo_metadata for project_id column
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'project_id' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.project_id (default=1)")
            cur.execute("""
                ALTER TABLE photo_metadata
                ADD COLUMN project_id INTEGER NOT NULL DEFAULT 1
            """)

        conn.commit()
        self.logger.info("✓ Project ID columns added successfully")

    def _add_file_hash_column_if_missing(self, conn: sqlite3.Connection):
        """
        Add file_hash column to photo_metadata if it doesn't exist.

        This is the core of the v4.0.0 migration - adds file_hash for duplicate detection
        during mobile device imports.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_metadata for file_hash column
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'file_hash' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.file_hash")
            cur.execute("""
                ALTER TABLE photo_metadata
                ADD COLUMN file_hash TEXT
            """)

        conn.commit()
        self.logger.info("✓ File hash column added successfully")


def get_migration_status(db_connection) -> Dict[str, Any]:
    """
    Get comprehensive migration status for a database.

    Args:
        db_connection: DatabaseConnection instance

    Returns:
        dict: Migration status information
    """
    manager = MigrationManager(db_connection)

    current = manager.get_current_version()
    target = manager.get_target_version()
    needs_migration = manager.needs_migration()
    pending = manager.get_pending_migrations()
    history = manager.get_migration_history()

    return {
        "current_version": current,
        "target_version": target,
        "needs_migration": needs_migration,
        "pending_count": len(pending),
        "pending_migrations": [
            {"version": m.version, "description": m.description}
            for m in pending
        ],
        "applied_count": len(history),
        "migration_history": history
    }
