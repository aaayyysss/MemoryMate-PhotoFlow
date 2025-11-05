# services/__init__.py
# Version 01.00.02.00 dated 20251105
# Service layer package - Business logic separated from UI and data access

from .photo_scan_service import (
    PhotoScanService,
    ScanResult,
    ScanProgress
)

from .scan_worker_adapter import (
    ScanWorkerAdapter,
    ScanWorker  # Backward compatibility alias
)

from .metadata_service import (
    MetadataService,
    ImageMetadata
)

from .thumbnail_service import (
    ThumbnailService,
    LRUCache,
    get_thumbnail_service
)

from .photo_deletion_service import (
    PhotoDeletionService,
    DeletionResult
)

from .search_service import (
    SearchService,
    SearchCriteria,
    SearchResult
)

from .tag_service import (
    TagService,
    get_tag_service
)

__all__ = [
    # Scanning
    'PhotoScanService',
    'ScanResult',
    'ScanProgress',
    'ScanWorkerAdapter',
    'ScanWorker',

    # Metadata
    'MetadataService',
    'ImageMetadata',

    # Thumbnails
    'ThumbnailService',
    'LRUCache',
    'get_thumbnail_service',

    # Deletion
    'PhotoDeletionService',
    'DeletionResult',

    # Search
    'SearchService',
    'SearchCriteria',
    'SearchResult',

    # Tags
    'TagService',
    'get_tag_service',
]
