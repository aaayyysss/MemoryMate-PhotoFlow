# services/__init__.py
# Version 01.00.01.00 dated 20251102
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
]
