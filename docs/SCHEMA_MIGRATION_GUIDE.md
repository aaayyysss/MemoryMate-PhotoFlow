# MemoryMate-PhotoFlow Schema Migration Guide

**Version**: 2.0.0
**Date**: 2025-11-03
**Status**: Production Ready

---

## Overview

MemoryMate-PhotoFlow has undergone a major architectural refactoring to improve maintainability, testability, and extensibility. As part of this refactoring, database schema management has been moved from the legacy `reference_db.py` module to a new repository layer.

**Key Changes:**
- Schema definition centralized in `repository/schema.py`
- Automatic schema migrations via `repository/migrations.py`
- Automatic schema creation/upgrade on application startup
- Single source of truth for database structure

**Good News**: All changes are **100% backward compatible**. Your existing databases will be automatically migrated on first run.

---

## What's New

### Repository Layer (New)

The new repository layer provides:
- **Centralized schema management** - One place for all table definitions
- **Automatic migrations** - Legacy databases upgraded automatically
- **Version tracking** - `schema_version` table tracks applied migrations
- **Self-contained** - No dependency on legacy code

**Files:**
- `repository/schema.py` - Complete schema definition (v2.0.0)
- `repository/migrations.py` - Migration system
- `repository/base_repository.py` - Database connection and schema initialization

### Legacy Code (Deprecated but Functional)

The legacy `reference_db.py` module:
- ✅ Still works exactly as before
- ✅ Now uses repository layer for schema management
- ⚠️ `_ensure_db()` method deprecated (will be removed in future)
- ⚠️ Direct schema management will be removed in v3.0.0

---

## Migration Path

### Automatic Migration (Recommended)

**For most users, no action is required.** The application will automatically:

1. Detect your database version on startup
2. Apply any pending migrations
3. Record migration history in `schema_version` table

**Migration Flow:**
```
Legacy Database (v1.0)
  ↓
[Auto-detected on startup]
  ↓
Migration v1.5.0 Applied
  - Adds: created_ts, created_date, created_year columns
  ↓
Migration v2.0.0 Applied
  - Creates: schema_version table
  - Creates: all missing tables (if any)
  - Creates: all indexes
  ↓
Current Database (v2.0.0)
```

### First Run Experience

**Existing Users:**
```
Starting MemoryMate-PhotoFlow...
→ Detected database version: 1.0.0
→ Applying migration 1.5.0... ✓ (0.03s)
→ Applying migration 2.0.0... ✓ (0.09s)
→ Database migrated to v2.0.0
→ Application ready
```

**New Users:**
```
Starting MemoryMate-PhotoFlow...
→ No database found
→ Creating fresh database v2.0.0... ✓
→ Application ready
```

---

## Database Version Check

### Check Your Database Version

**Via Application Log:**
Look for these messages in `app_log.txt`:
```
[INFO] Schema check: current=1.0.0, target=2.0.0
[INFO] Migrating database from 1.0.0 to 2.0.0
[INFO] ✓ Migrations completed: 2 applied successfully
```

**Via Python:**
```python
from reference_db import ReferenceDB
from repository.migrations import get_migration_status

db = ReferenceDB()  # Opens your database
status = get_migration_status(db._db_connection)

print(f"Current version: {status['current_version']}")
print(f"Target version: {status['target_version']}")
print(f"Needs migration: {status['needs_migration']}")
print(f"Migration history: {status['migration_history']}")
```

**Via SQL:**
```sql
-- Check if schema_version table exists
SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version';

-- View migration history
SELECT version, description, applied_at FROM schema_version ORDER BY applied_at;
```

---

## Schema Changes

### New Tables (v2.0.0)

**schema_version** (New)
- Tracks all applied schema migrations
- Records version, description, and timestamp

```sql
CREATE TABLE schema_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);
```

### New Columns (v1.5.0 → v2.0.0)

**photo_metadata** table additions:
- `created_ts` (INTEGER) - Unix timestamp for photo date
- `created_date` (TEXT) - ISO date (YYYY-MM-DD) for photo
- `created_year` (INTEGER) - Year extracted for quick filtering
- `metadata_status` (TEXT) - Status of metadata extraction
- `metadata_fail_count` (INTEGER) - Number of failed extraction attempts

**Indexes Added:**
- `idx_photo_created_year` - Fast year-based queries
- `idx_photo_created_date` - Fast date-based queries
- `idx_photo_created_ts` - Fast timestamp-based queries

### All Existing Data Preserved

✅ All your existing photos, folders, projects, and face data are preserved
✅ No data loss during migration
✅ Backward compatible with pre-migration queries

---

## For Developers

### Using the Repository Layer

**Old Way (Deprecated):**
```python
from reference_db import ReferenceDB

db = ReferenceDB()
db._ensure_db()  # ⚠️ Deprecated - shows warning
```

**New Way (Recommended):**
```python
from repository.base_repository import DatabaseConnection
from repository.photo_repository import PhotoRepository

# Schema created automatically
db_conn = DatabaseConnection("reference_data.db", auto_init=True)
photo_repo = PhotoRepository(db_conn)

# Use repository methods
photos = photo_repo.get_all()
```

### Adding Custom Migrations

To add a new migration for future schema changes:

```python
# repository/migrations.py

MIGRATION_2_1_0 = Migration(
    version="2.1.0",
    description="Add new feature columns",
    sql="""
    -- Your SQL changes here
    ALTER TABLE photo_metadata ADD COLUMN new_field TEXT;
    CREATE INDEX idx_new_field ON photo_metadata(new_field);

    -- Record migration
    INSERT OR REPLACE INTO schema_version (version, description)
    VALUES ('2.1.0', 'Add new feature columns');
    """
)

# Add to ALL_MIGRATIONS list
ALL_MIGRATIONS = [
    MIGRATION_1_5_0,
    MIGRATION_2_0_0,
    MIGRATION_2_1_0,  # New migration
]
```

The migration system will automatically detect and apply your new migration.

---

## Troubleshooting

### Migration Fails

**Symptom:** Application shows migration error on startup

**Solution:**
1. Check `app_log.txt` for detailed error messages
2. Backup your `reference_data.db` file
3. Check database file permissions (must be writable)
4. Try manual migration:
   ```python
   from repository.base_repository import DatabaseConnection
   from repository.migrations import MigrationManager

   db = DatabaseConnection("reference_data.db", auto_init=False)
   manager = MigrationManager(db)
   results = manager.apply_all_migrations()

   for r in results:
       print(f"{r['version']}: {r['status']}")
   ```

### Database Locked

**Symptom:** "database is locked" error

**Solution:**
1. Close any other applications accessing the database
2. Check for background workers/threads
3. Restart application

### Deprecation Warnings

**Symptom:** Seeing warnings about `_ensure_db()` being deprecated

**Solution:** This is informational only. The method still works, but:
- Update code to use repository layer directly (recommended)
- Warnings can be ignored for now (will be removed in v3.0.0)

---

## Rollback (Advanced)

**Generally not needed** - migrations are designed to be forward-only.

If you absolutely must rollback:

1. **Backup first:**
   ```bash
   cp reference_data.db reference_data.db.backup
   ```

2. **Remove new columns manually** (SQLite limitation):
   ```sql
   -- SQLite doesn't support DROP COLUMN easily
   -- You would need to recreate the table without the column
   -- This is complex and not recommended
   ```

3. **Restore from backup:**
   ```bash
   cp reference_data.db.backup reference_data.db
   ```

**Note:** Rolling back is not recommended as it may cause data loss. Contact support if needed.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2025-11-03 | Repository layer schema, migration system, schema_version table |
| 1.5.0 | 2025-11-03 | Added created_ts, created_date, created_year columns |
| 1.0.0 | 2025-10-31 | Legacy schema (reference_db.py) |

---

## FAQ

### Q: Will my existing data be lost?
**A:** No. All migrations are non-destructive and preserve existing data.

### Q: How long does migration take?
**A:** Very fast - typically 0.1-0.5 seconds for most databases.

### Q: Can I skip migrations?
**A:** No. Migrations must be applied in order to ensure schema consistency.

### Q: What if I have a very old database?
**A:** The migration system will automatically detect your version and apply all necessary migrations in sequence.

### Q: Is this safe for production?
**A:** Yes. The migration system has been thoroughly tested with:
- Fresh databases (v0.0.0 → v2.0.0)
- Legacy databases (v1.0.0 → v2.0.0)
- Already-migrated databases (v2.0.0 → v2.0.0)

### Q: Can I manually trigger migration?
**A:** Yes, but it's not necessary. Migrations happen automatically on startup. If needed:
```python
from repository.base_repository import DatabaseConnection
db = DatabaseConnection("reference_data.db", auto_init=True)  # Triggers migration
```

### Q: Where are migrations logged?
**A:**
- Application log: `app_log.txt`
- Database table: `schema_version`
- Console output (if enabled)

---

## Support

**Issues?** Report at: https://github.com/aaayyysss/MemoryMate-PhotoFlow/issues

**Documentation:** See `docs/ARCHITECTURE.md` for technical details

**Migration System:** See `docs/ARCHITECTURAL_AUDIT_REPORT.md` for audit findings

---

## Summary

✅ **Zero action required** for most users
✅ **Automatic migration** on first run
✅ **100% backward compatible**
✅ **All data preserved**
✅ **Production ready**

The migration to repository layer schema is seamless and improves the application's architecture significantly. Your databases will be automatically upgraded on next run.

---

**Document Version**: 1.0
**Last Updated**: 2025-11-03
**Author**: MemoryMate-PhotoFlow Development Team
