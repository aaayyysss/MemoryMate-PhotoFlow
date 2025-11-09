# repository/schema.py
# Version 2.0.0 dated 20251103
# Centralized database schema definition for repository layer
#
# This module provides the complete database schema for MemoryMate-PhotoFlow.
# It is the single source of truth for schema creation and versioning.

"""
Centralized database schema definition for repository layer.

This schema is extracted from the legacy reference_db.py and serves as
the canonical definition for all database tables, indexes, and constraints.

Schema Version: 2.0.0
- Includes all 13 tables from production
- Includes all foreign key constraints
- Includes all performance indexes
- Includes created_ts/created_date/created_year columns (previously migrations)
- Adds schema_version tracking table
"""

SCHEMA_VERSION = "3.2.0"

# Complete schema SQL - executed as a script for new databases
SCHEMA_SQL = """
-- ============================================================================
-- SCHEMA VERSION TRACKING
-- ============================================================================
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- Insert initial version marker
INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('3.0.0', 'Added project_id to photo_folders and photo_metadata for clean project isolation');

INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('3.1.0', 'Added project_id to tags table for proper tag isolation between projects');

INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('3.2.0', 'Added complete video infrastructure (video_metadata, project_videos, video_tags)');

-- ============================================================================
-- FACE RECOGNITION TABLES
-- ============================================================================

-- Reference images for face recognition
CREATE TABLE IF NOT EXISTS reference_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);

-- Match audit logging for face recognition
CREATE TABLE IF NOT EXISTS match_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    matched_label TEXT,
    confidence REAL,
    match_mode TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Label thresholds for face recognition
CREATE TABLE IF NOT EXISTS reference_labels (
    label TEXT PRIMARY KEY,
    folder_path TEXT NOT NULL,
    threshold REAL DEFAULT 0.3
);

-- ============================================================================
-- PROJECT ORGANIZATION TABLES
-- ============================================================================

-- Projects (top-level organizational unit)
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Branches (sub-groups within projects)
CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key)
);

-- Project images (many-to-many: projects/branches to images)
CREATE TABLE IF NOT EXISTS project_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT,
    image_path TEXT NOT NULL,
    label TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Face crops (face thumbnails for each branch)
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

-- Face branch representatives (cluster centroids and representative images)
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

-- Export history (tracks photo export operations)
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

-- ============================================================================
-- PHOTO LIBRARY TABLES (Core photo management)
-- ============================================================================

-- Photo folders (hierarchical folder structure with project ownership)
CREATE TABLE IF NOT EXISTS photo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    parent_id INTEGER NULL,
    project_id INTEGER NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)
);

-- Photo metadata (main photo index with all metadata and project ownership)
CREATE TABLE IF NOT EXISTS photo_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    size_kb REAL,
    modified TEXT,
    width INTEGER,
    height INTEGER,
    embedding BLOB,
    date_taken TEXT,
    tags TEXT,
    updated_at TEXT,
    metadata_status TEXT DEFAULT 'pending',
    metadata_fail_count INTEGER DEFAULT 0,
    created_ts INTEGER,
    created_date TEXT,
    created_year INTEGER,
    FOREIGN KEY(folder_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)
);

-- ============================================================================
-- TAGGING TABLES (Normalized tag structure)
-- ============================================================================

-- Tags (tag definitions)
-- Schema v3.1.0: Added project_id for proper tag isolation between projects
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
    project_id INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(name, project_id)
);

-- Photo tags (many-to-many: photos to tags)
CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (photo_id, tag_id),
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- ============================================================================
-- VIDEO TABLES (Schema v3.2.0: Complete video infrastructure)
-- ============================================================================

-- Video metadata (mirrors photo_metadata structure for videos)
CREATE TABLE IF NOT EXISTS video_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,

    -- File metadata
    size_kb REAL,
    modified TEXT,

    -- Video-specific metadata
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    codec TEXT,
    bitrate INTEGER,

    -- Timestamps (for date-based browsing)
    date_taken TEXT,
    created_ts INTEGER,
    created_date TEXT,
    created_year INTEGER,
    updated_at TEXT,

    -- Processing status
    metadata_status TEXT DEFAULT 'pending',
    metadata_fail_count INTEGER DEFAULT 0,
    thumbnail_status TEXT DEFAULT 'pending',

    FOREIGN KEY (folder_id) REFERENCES photo_folders(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)
);

-- Project videos (mirrors project_images for videos)
CREATE TABLE IF NOT EXISTS project_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT,
    video_path TEXT NOT NULL,
    label TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key, video_path)
);

-- Video tags (many-to-many: videos to tags)
CREATE TABLE IF NOT EXISTS video_tags (
    video_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (video_id, tag_id),
    FOREIGN KEY (video_id) REFERENCES video_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Face crops indexes
CREATE INDEX IF NOT EXISTS idx_face_crops_proj ON face_crops(project_id);
CREATE INDEX IF NOT EXISTS idx_face_crops_proj_branch ON face_crops(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_face_crops_proj_rep ON face_crops(project_id, is_representative);

-- Face branch reps indexes
CREATE INDEX IF NOT EXISTS idx_fbreps_proj ON face_branch_reps(project_id);
CREATE INDEX IF NOT EXISTS idx_fbreps_proj_branch ON face_branch_reps(project_id, branch_key);

-- Branches indexes
CREATE INDEX IF NOT EXISTS idx_branches_project ON branches(project_id);
CREATE INDEX IF NOT EXISTS idx_branches_key ON branches(project_id, branch_key);

-- Project images indexes
CREATE INDEX IF NOT EXISTS idx_projimgs_project ON project_images(project_id);
CREATE INDEX IF NOT EXISTS idx_projimgs_branch ON project_images(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_projimgs_path ON project_images(image_path);

-- Photo folders indexes
CREATE INDEX IF NOT EXISTS idx_photo_folders_project ON photo_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_photo_folders_parent ON photo_folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_photo_folders_path ON photo_folders(path);

-- Photo metadata indexes (project_id for fast filtering)
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project ON photo_metadata(project_id);

-- Photo metadata indexes (date and metadata)
CREATE INDEX IF NOT EXISTS idx_meta_date ON photo_metadata(date_taken);
CREATE INDEX IF NOT EXISTS idx_meta_modified ON photo_metadata(modified);
CREATE INDEX IF NOT EXISTS idx_meta_updated ON photo_metadata(updated_at);
CREATE INDEX IF NOT EXISTS idx_meta_folder ON photo_metadata(folder_id);
CREATE INDEX IF NOT EXISTS idx_meta_status ON photo_metadata(metadata_status);

-- Photo metadata indexes (created_* columns for date-based browsing)
CREATE INDEX IF NOT EXISTS idx_photo_created_year ON photo_metadata(created_year);
CREATE INDEX IF NOT EXISTS idx_photo_created_date ON photo_metadata(created_date);
CREATE INDEX IF NOT EXISTS idx_photo_created_ts ON photo_metadata(created_ts);

-- Tag indexes (v3.1.0: Added project_id indexes)
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_tags_project ON tags(project_id);
CREATE INDEX IF NOT EXISTS idx_tags_project_name ON tags(project_id, name);
CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);

-- Video indexes (v3.2.0: Video infrastructure)
CREATE INDEX IF NOT EXISTS idx_video_metadata_project ON video_metadata(project_id);
CREATE INDEX IF NOT EXISTS idx_video_metadata_folder ON video_metadata(folder_id);
CREATE INDEX IF NOT EXISTS idx_video_metadata_date ON video_metadata(date_taken);
CREATE INDEX IF NOT EXISTS idx_video_metadata_year ON video_metadata(created_year);
CREATE INDEX IF NOT EXISTS idx_video_metadata_status ON video_metadata(metadata_status);

CREATE INDEX IF NOT EXISTS idx_project_videos_project ON project_videos(project_id);
CREATE INDEX IF NOT EXISTS idx_project_videos_branch ON project_videos(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_project_videos_path ON project_videos(video_path);

CREATE INDEX IF NOT EXISTS idx_video_tags_video ON video_tags(video_id);
CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);
"""


def get_schema_sql() -> str:
    """
    Return the complete schema SQL for database initialization.

    Returns:
        str: SQL script containing all CREATE TABLE and CREATE INDEX statements
    """
    return SCHEMA_SQL


def get_schema_version() -> str:
    """
    Return the current schema version.

    Returns:
        str: Schema version string (e.g., "2.0.0")
    """
    return SCHEMA_VERSION


def get_expected_tables() -> list[str]:
    """
    Return list of expected table names in the schema.

    Returns:
        list[str]: List of table names that should exist
    """
    return [
        "schema_version",
        "reference_entries",
        "match_audit",
        "reference_labels",
        "projects",
        "branches",
        "project_images",
        "face_crops",
        "face_branch_reps",
        "export_history",
        "photo_folders",
        "photo_metadata",
        "tags",
        "photo_tags",
        # Video tables (v3.2.0)
        "video_metadata",
        "project_videos",
        "video_tags",
    ]


def get_expected_indexes() -> list[str]:
    """
    Return list of expected index names in the schema.

    Returns:
        list[str]: List of index names that should exist
    """
    return [
        "idx_face_crops_proj",
        "idx_face_crops_proj_branch",
        "idx_face_crops_proj_rep",
        "idx_fbreps_proj",
        "idx_fbreps_proj_branch",
        "idx_branches_project",
        "idx_branches_key",
        "idx_projimgs_project",
        "idx_projimgs_branch",
        "idx_projimgs_path",
        "idx_photo_folders_project",
        "idx_photo_folders_parent",
        "idx_photo_folders_path",
        "idx_photo_metadata_project",
        "idx_meta_date",
        "idx_meta_modified",
        "idx_meta_updated",
        "idx_meta_folder",
        "idx_meta_status",
        "idx_photo_created_year",
        "idx_photo_created_date",
        "idx_photo_created_ts",
        "idx_tags_name",
        "idx_tags_project",
        "idx_tags_project_name",
        "idx_photo_tags_photo",
        "idx_photo_tags_tag",
        # Video indexes (v3.2.0)
        "idx_video_metadata_project",
        "idx_video_metadata_folder",
        "idx_video_metadata_date",
        "idx_video_metadata_year",
        "idx_video_metadata_status",
        "idx_project_videos_project",
        "idx_project_videos_branch",
        "idx_project_videos_path",
        "idx_video_tags_video",
        "idx_video_tags_tag",
    ]


# Schema migration support (for future use)
MIGRATIONS = {
    "1.0.0": {
        "description": "Legacy schema from reference_db.py",
        "sql": "-- Legacy schema, no migration needed"
    },
    "2.0.0": {
        "description": "Repository layer schema with all tables and indexes",
        "sql": "-- Superseded by 3.0.0"
    },
    "3.0.0": {
        "description": "Added project_id to photo_folders and photo_metadata for clean project isolation",
        "sql": SCHEMA_SQL
    }
}


def get_migration(from_version: str, to_version: str) -> str | None:
    """
    Get migration SQL for upgrading from one version to another.

    Args:
        from_version: Starting schema version
        to_version: Target schema version

    Returns:
        str: Migration SQL, or None if no migration exists
    """
    # For now, we only support creating new databases with 2.0.0
    # Future: Add incremental migration support
    if to_version in MIGRATIONS:
        return MIGRATIONS[to_version]["sql"]
    return None
