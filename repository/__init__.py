# repository/__init__.py
# Version 01.00.00.00 dated 20251102
# Repository package for data access layer

from .base_repository import (
    BaseRepository,
    DatabaseConnection,
    TransactionContext
)

from .photo_repository import PhotoRepository
from .folder_repository import FolderRepository
from .project_repository import ProjectRepository

__all__ = [
    # Base classes
    'BaseRepository',
    'DatabaseConnection',
    'TransactionContext',

    # Concrete repositories
    'PhotoRepository',
    'FolderRepository',
    'ProjectRepository',
]
