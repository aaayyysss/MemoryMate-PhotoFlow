# services/__init__.py
# Version 01.00.00.00 dated 20251102
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

__all__ = [
    'PhotoScanService',
    'ScanResult',
    'ScanProgress',
    'ScanWorkerAdapter',
    'ScanWorker',
]
