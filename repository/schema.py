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

SCHEMA_VERSION = "5.0.0"

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

INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('3.3.0', 'Added compound indexes for query optimization (project_id + folder/date patterns)');

INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('5.0.0', 'Added mobile device tracking: devices, import sessions, and file provenance');

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
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key, image_path)
);

-- Face crops (face thumbnails for each branch)
-- Phase 5: Added embedding column for face recognition clustering
CREATE TABLE IF NOT EXISTS face_crops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT,
    image_path TEXT NOT NULL,
    crop_path TEXT NOT NULL,
    embedding BLOB,
    bbox_x INTEGER,
    bbox_y INTEGER,
    bbox_w INTEGER,
    bbox_h INTEGER,
    confidence REAL,
    is_representative INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, image_path, bbox_x, bbox_y, bbox_w, bbox_h)
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

-- Face merge history (for undo functionality)
CREATE TABLE IF NOT EXISTS face_merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    target_branch TEXT NOT NULL,
    source_branches TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
    file_hash TEXT,
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
-- MOBILE DEVICE TRACKING TABLES (Schema v5.0.0)
-- ============================================================================

-- Mobile devices registry (tracks all connected devices)
CREATE TABLE IF NOT EXISTS mobile_devices (
    device_id TEXT PRIMARY KEY,           -- Unique device identifier (MTP serial, iOS UUID, Volume GUID)
    device_name TEXT NOT NULL,            -- User-friendly name ("Samsung Galaxy S22", "John's iPhone")
    device_type TEXT NOT NULL,            -- Device type: "android", "ios", "camera", "usb", "sd_card"
    serial_number TEXT,                   -- Physical serial number (if available)
    volume_guid TEXT,                     -- Volume GUID for removable storage (Windows)
    mount_point TEXT,                     -- Last known mount path ("/media/user/phone")
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When device first connected
    last_seen TIMESTAMP,                  -- Last time device was detected
    last_import_session INTEGER,          -- ID of most recent import session
    total_imports INTEGER DEFAULT 0,      -- Total number of import sessions
    total_photos_imported INTEGER DEFAULT 0,  -- Cumulative photo count
    total_videos_imported INTEGER DEFAULT 0,  -- Cumulative video count
    notes TEXT,                           -- User notes about device
    -- Phase 4: Auto-import preferences
    auto_import BOOLEAN DEFAULT 0,        -- Enable auto-import for this device
    auto_import_folder TEXT DEFAULT NULL, -- Which folder to auto-import from (e.g., "Camera")
    last_auto_import TIMESTAMP DEFAULT NULL,  -- Last time auto-import ran
    auto_import_enabled_date TIMESTAMP DEFAULT NULL  -- When auto-import was enabled
);

-- Import sessions (tracks each import operation)
CREATE TABLE IF NOT EXISTS import_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,              -- Which device was imported from
    project_id INTEGER NOT NULL,          -- Target project
    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    import_type TEXT DEFAULT 'manual',    -- "manual", "auto", "incremental"
    photos_imported INTEGER DEFAULT 0,
    videos_imported INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    bytes_imported INTEGER DEFAULT 0,
    duration_seconds INTEGER,
    status TEXT DEFAULT 'completed',      -- "in_progress", "completed", "partial", "failed"
    error_message TEXT,
    FOREIGN KEY (device_id) REFERENCES mobile_devices(device_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Device files (tracks all files ever seen on devices)
CREATE TABLE IF NOT EXISTS device_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,              -- Which device this file is on
    device_path TEXT NOT NULL,            -- Original path on device (e.g., "/DCIM/Camera/IMG_001.jpg")
    device_folder TEXT,                   -- Folder name on device ("Camera", "Screenshots", "WhatsApp")
    file_hash TEXT NOT NULL,              -- SHA256 hash for duplicate detection
    file_size INTEGER,                    -- File size in bytes
    file_mtime TIMESTAMP,                 -- File modification time on device
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When first detected
    last_seen TIMESTAMP,                  -- Last time seen on device
    import_status TEXT DEFAULT 'new',     -- "new", "imported", "skipped", "deleted"
    local_photo_id INTEGER,               -- Link to photo_metadata.id (if imported)
    local_video_id INTEGER,               -- Link to video_metadata.id (if imported)
    import_session_id INTEGER,            -- Which session imported this file
    FOREIGN KEY (device_id) REFERENCES mobile_devices(device_id) ON DELETE CASCADE,
    FOREIGN KEY (import_session_id) REFERENCES import_sessions(id) ON DELETE SET NULL,
    FOREIGN KEY (local_photo_id) REFERENCES photo_metadata(id) ON DELETE SET NULL,
    FOREIGN KEY (local_video_id) REFERENCES video_metadata(id) ON DELETE SET NULL,
    UNIQUE(device_id, device_path)
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

-- Photo metadata indexes (file_hash for duplicate detection during imports)
CREATE INDEX IF NOT EXISTS idx_photo_metadata_hash ON photo_metadata(file_hash);

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
CREATE INDEX IF NOT EXISTS idx_video_thumbnail_status ON video_metadata(thumbnail_status);

CREATE INDEX IF NOT EXISTS idx_project_videos_project ON project_videos(project_id);
CREATE INDEX IF NOT EXISTS idx_project_videos_branch ON project_videos(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_project_videos_path ON project_videos(video_path);

CREATE INDEX IF NOT EXISTS idx_video_tags_video ON video_tags(video_id);
CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);

-- Compound indexes for performance (v3.3.0: Query optimization)
-- These indexes optimize common filtering patterns by project + another column
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project_folder ON photo_metadata(project_id, folder_id);
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project_date ON photo_metadata(project_id, created_year, created_date);
CREATE INDEX IF NOT EXISTS idx_video_metadata_project_folder ON video_metadata(project_id, folder_id);
CREATE INDEX IF NOT EXISTS idx_video_metadata_project_date ON video_metadata(project_id, created_year, created_date);
CREATE INDEX IF NOT EXISTS idx_video_metadata_project_thumb_status ON video_metadata(project_id, thumbnail_status);
CREATE INDEX IF NOT EXISTS idx_video_metadata_project_meta_status ON video_metadata(project_id, metadata_status);
CREATE INDEX IF NOT EXISTS idx_project_images_project_branch ON project_images(project_id, branch_key, image_path);
CREATE INDEX IF NOT EXISTS idx_photo_folders_project_parent ON photo_folders(project_id, parent_id);

-- Mobile device tracking indexes (v5.0.0: Device import tracking)
CREATE INDEX IF NOT EXISTS idx_mobile_devices_type ON mobile_devices(device_type);
CREATE INDEX IF NOT EXISTS idx_mobile_devices_last_seen ON mobile_devices(last_seen);
CREATE INDEX IF NOT EXISTS idx_mobile_devices_auto_import ON mobile_devices(auto_import) WHERE auto_import = 1;

CREATE INDEX IF NOT EXISTS idx_import_sessions_device ON import_sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_import_sessions_project ON import_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_import_sessions_date ON import_sessions(import_date);
CREATE INDEX IF NOT EXISTS idx_import_sessions_status ON import_sessions(status);

CREATE INDEX IF NOT EXISTS idx_device_files_device ON device_files(device_id);
CREATE INDEX IF NOT EXISTS idx_device_files_hash ON device_files(file_hash);
CREATE INDEX IF NOT EXISTS idx_device_files_status ON device_files(device_id, import_status);
CREATE INDEX IF NOT EXISTS idx_device_files_photo ON device_files(local_photo_id);
CREATE INDEX IF NOT EXISTS idx_device_files_video ON device_files(local_video_id);
CREATE INDEX IF NOT EXISTS idx_device_files_session ON device_files(import_session_id);
CREATE INDEX IF NOT EXISTS idx_device_files_last_seen ON device_files(device_id, last_seen);
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
        # Mobile device tracking tables (v5.0.0)
        "mobile_devices",
        "import_sessions",
        "device_files",
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
        # Compound indexes (v3.3.0)
        "idx_photo_metadata_project_folder",
        "idx_photo_metadata_project_date",
        "idx_video_metadata_project_folder",
        "idx_video_metadata_project_date",
        "idx_project_images_project_branch",
        "idx_photo_folders_project_parent",
        # Mobile device tracking indexes (v5.0.0)
        "idx_mobile_devices_type",
        "idx_mobile_devices_last_seen",
        "idx_mobile_devices_auto_import",
        "idx_import_sessions_device",
        "idx_import_sessions_project",
        "idx_import_sessions_date",
        "idx_import_sessions_status",
        "idx_device_files_device",
        "idx_device_files_hash",
        "idx_device_files_status",
        "idx_device_files_photo",
        "idx_device_files_video",
        "idx_device_files_session",
        "idx_device_files_last_seen",
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
