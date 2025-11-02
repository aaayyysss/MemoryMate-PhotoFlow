# Logging Migration Guide

## Overview

This guide helps migrate from `print()` statements to the centralized logging framework.

## Quick Start

### 1. Add logging import to your module

```python
from logging_config import get_logger

# At module level (after imports, before classes/functions)
logger = get_logger(__name__)
```

### 2. Replace print statements

| Old Code | New Code | When to Use |
|----------|----------|-------------|
| `print(f"Processing {file}")` | `logger.debug(f"Processing {file}")` | Detailed debugging info |
| `print("[INFO] Scan complete")` | `logger.info("Scan complete")` | General information |
| `print("WARNING: Cache full")` | `logger.warning("Cache full")` | Warnings that don't stop execution |
| `print("ERROR:", e)` | `logger.error(f"Operation failed: {e}")` | Errors that need attention |
| N/A | `logger.critical("Database corrupted!")` | Fatal errors |

### 3. Log exceptions properly

**OLD:**
```python
try:
    do_something()
except Exception as e:
    print(f"Error: {e}")
    pass  # ❌ Silently swallows error
```

**NEW:**
```python
try:
    do_something()
except Exception as e:
    logger.error(f"Operation failed: {e}", exc_info=True)
    # Handle the error appropriately
```

The `exc_info=True` parameter automatically includes the full stack trace in the log file.

## Log Levels Explained

### DEBUG
- **Use for:** Detailed diagnostic information
- **Visible:** Only when log_level=DEBUG in settings
- **Examples:**
  - Individual file processing steps
  - Cache hits/misses
  - SQL query text (for debugging)

```python
logger.debug(f"Cache hit for {path} (mtime={mtime})")
logger.debug(f"Executing SQL: {query}")
```

### INFO
- **Use for:** General informational messages
- **Visible:** When log_level=INFO or DEBUG
- **Examples:**
  - Scan started/completed
  - Number of files processed
  - Feature enabled/disabled

```python
logger.info(f"Scan complete: {photo_count} photos indexed")
logger.info(f"Thumbnail cache enabled")
```

### WARNING
- **Use for:** Something unexpected but recoverable
- **Visible:** When log_level=WARNING, INFO, or DEBUG
- **Examples:**
  - Missing EXIF data
  - Slow operation detected
  - Deprecated feature used

```python
logger.warning(f"No EXIF data found in {path}")
logger.warning(f"os.stat took {elapsed:.2f}s (possible network issue)")
```

### ERROR
- **Use for:** Error that prevents a specific operation
- **Visible:** Always (except if log_level=CRITICAL)
- **Examples:**
  - Failed to read image
  - Database query failed
  - File not found

```python
logger.error(f"Failed to decode image: {path}", exc_info=True)
logger.error(f"Database connection failed: {e}")
```

### CRITICAL
- **Use for:** Fatal errors that crash the application
- **Visible:** Always
- **Examples:**
  - Database file corrupted
  - Required dependency missing
  - Out of memory

```python
logger.critical(f"Database integrity check failed!")
```

## Common Migration Patterns

### Pattern 1: Module-level initialization
```python
# OLD
def scan_repository(root_folder):
    print(f"[SCAN] Starting scan of {root_folder}")
    # ...

# NEW
from logging_config import get_logger
logger = get_logger(__name__)

def scan_repository(root_folder):
    logger.info(f"Starting scan of {root_folder}")
    # ...
```

### Pattern 2: Class-based logging
```python
# OLD
class ScanWorker(QObject):
    def run(self):
        print(f"[ScanWorker] processing {file}")

# NEW
from logging_config import get_logger

class ScanWorker(QObject):
    def __init__(self):
        super().__init__()
        self.logger = get_logger(self.__class__.__name__)

    def run(self):
        self.logger.debug(f"Processing {file}")
```

### Pattern 3: Progress messages
```python
# OLD
print(f"[SCAN] {processed}/{total} files ({pct}%)")

# NEW
if processed % 100 == 0:  # Log every 100 files
    logger.info(f"Progress: {processed}/{total} files ({pct}%)")
```

### Pattern 4: Exception handling
```python
# OLD
try:
    with Image.open(path) as img:
        width, height = img.size
except Exception:
    pass  # ❌ Error hidden!

# NEW
try:
    with Image.open(path) as img:
        width, height = img.size
except Exception as e:
    logger.warning(f"Could not read dimensions from {path}: {e}")
    width = height = None
```

### Pattern 5: Conditional debugging
```python
# OLD
if self.settings.get("db_debug_logging"):
    print(f"[DB] Executing: {sql}")

# NEW
logger.debug(f"Executing SQL: {sql}")
# User can enable this by setting log_level=DEBUG in settings
```

## Configuration

Users can control logging via `photo_app_settings.json`:

```json
{
  "log_level": "INFO",           // DEBUG, INFO, WARNING, ERROR, CRITICAL
  "log_to_console": true,        // Show logs in terminal
  "log_colored_output": true     // Use colors in console
}
```

## Log File Location

Logs are written to:
- **File:** `app_log.txt` (in current working directory)
- **Rotation:** Automatically rotates at 10 MB
- **Retention:** Keeps last 5 log files (`app_log.txt.1`, `.2`, etc.)

## Best Practices

### ✅ DO:
- Use appropriate log levels
- Include context in messages (file paths, counts, error details)
- Use `exc_info=True` for exceptions
- Log at decision points (branching logic)
- Log start/end of long operations

### ❌ DON'T:
- Log inside tight loops (use modulo: `if i % 100 == 0`)
- Include sensitive data (passwords, API keys)
- Log to both print() and logger (choose one)
- Use bare `except: pass` (always log errors)
- Include emoji in log messages (use plain text)

## Performance Considerations

Logging is **very fast** but not free:

```python
# ❌ BAD: Logs every single file (could be 100K+ messages)
for file in all_files:
    logger.debug(f"Processing {file}")

# ✅ GOOD: Batch logging
for i, file in enumerate(all_files):
    # process file
    if i % 100 == 0:
        logger.info(f"Progress: {i}/{len(all_files)} files")
```

## Testing Your Changes

After migrating a module:

1. Set `log_level: "DEBUG"` in settings
2. Run the feature
3. Check `app_log.txt` for expected messages
4. Verify no important information was lost
5. Set back to `log_level: "INFO"`

## Priority Modules for Migration

Based on audit findings, migrate in this order:

1. ✅ `main_qt.py` (startup logging) - **DONE**
2. **HIGH PRIORITY:**
   - `db_writer.py` - Replace print statements
   - `main_window_qt.py` - ScanWorker logging
   - `app_services.py` - Service layer logging
   - `reference_db.py` - Database operations
3. **MEDIUM PRIORITY:**
   - `thumbnail_grid_qt.py` - Thumbnail loading
   - `thumb_cache_db.py` - Cache operations
   - `sidebar_qt.py` - UI interactions
4. **LOW PRIORITY:**
   - `preview_panel_qt.py` - Preview dialog
   - `settings_manager_qt.py` - Settings

## Example: Before & After

### Before (db_writer.py)
```python
def _do_upsert_chunk(self, rows: List[UpsertRow]):
    if not rows:
        return

    db = ReferenceDB()

    try:
        conn = db._connect()
        cur = conn.cursor()
        cur.executemany(sql_with_created, params_with_created)
        conn.commit()
        print(f"[DBWriter] committed {len(params_with_created)} rows")
    except Exception as e:
        print(f"[DBWriter] upsert failed: {e}")
```

### After (db_writer.py)
```python
from logging_config import get_logger

class DBWriter(QObject):
    def __init__(self):
        super().__init__()
        self.logger = get_logger(self.__class__.__name__)

    def _do_upsert_chunk(self, rows: List[UpsertRow]):
        if not rows:
            return

        db = ReferenceDB()

        try:
            conn = db._connect()
            cur = conn.cursor()
            cur.executemany(sql_with_created, params_with_created)
            conn.commit()
            self.logger.info(f"Committed {len(params_with_created)} rows")
        except Exception as e:
            self.logger.error(f"Upsert failed: {e}", exc_info=True)
```

## Questions?

See `logging_config.py` for implementation details or examples.
