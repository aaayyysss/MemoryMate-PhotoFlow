# repository/base_repository.py
# Version 01.00.00.00 dated 20251102
# Base repository pattern for data access layer

import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Generator
from logging_config import get_logger

logger = get_logger(__name__)


class DatabaseConnection:
    """
    Manages database connections with proper pooling and lifecycle management.

    This singleton class ensures:
    - Only one database file is used
    - Connections are properly configured (foreign keys, WAL mode)
    - Thread-safe access
    - Proper connection cleanup
    """

    _instance: Optional['DatabaseConnection'] = None
    _db_path: Optional[str] = None

    def __new__(cls, db_path: str = "reference_data.db"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: str = "reference_data.db"):
        if self._initialized:
            return

        self._db_path = db_path
        self._initialized = True
        logger.info(f"DatabaseConnection initialized with path: {db_path}")

    @contextmanager
    def get_connection(self, read_only: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a database connection as a context manager.

        Args:
            read_only: If True, opens connection in read-only mode

        Yields:
            sqlite3.Connection: Database connection

        Example:
            with db_conn.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM photos")
        """
        conn = None
        try:
            uri = f"file:{self._db_path}?mode=ro" if read_only else self._db_path
            conn = sqlite3.connect(uri if read_only else self._db_path,
                                   timeout=10.0,
                                   check_same_thread=False)

            # Configure connection
            conn.execute("PRAGMA foreign_keys = ON")

            # Enable WAL mode for better concurrency (write-ahead logging)
            if not read_only:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    logger.warning("Could not enable WAL mode")

            # Return dictionary-like rows for easier access
            conn.row_factory = self._dict_factory

            yield conn

        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}", exc_info=True)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Error closing connection: {e}")

    @staticmethod
    def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> Dict[str, Any]:
        """Convert row tuples to dictionaries using column names."""
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    def execute_script(self, script: str):
        """
        Execute a SQL script (for migrations, schema setup).

        Args:
            script: SQL script to execute
        """
        with self.get_connection() as conn:
            conn.executescript(script)
            conn.commit()
        logger.info("SQL script executed successfully")


class BaseRepository(ABC):
    """
    Abstract base class for all repositories.

    Repositories handle all database operations for a specific domain entity.
    This promotes:
    - Single Responsibility Principle
    - Testability (can mock repositories)
    - Clean separation between business logic and data access

    Usage:
        class PhotoRepository(BaseRepository):
            def get_by_id(self, photo_id: int) -> Optional[Dict]:
                with self.connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
                    return cur.fetchone()
    """

    def __init__(self, db_connection: Optional[DatabaseConnection] = None):
        """
        Initialize repository with database connection.

        Args:
            db_connection: Optional DatabaseConnection instance.
                          If None, uses default singleton.
        """
        self._db_connection = db_connection or DatabaseConnection()
        self.logger = get_logger(self.__class__.__name__)

    @contextmanager
    def connection(self, read_only: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a database connection for repository operations.

        Args:
            read_only: Whether to open in read-only mode

        Yields:
            Database connection
        """
        with self._db_connection.get_connection(read_only=read_only) as conn:
            yield conn

    @abstractmethod
    def _table_name(self) -> str:
        """Return the primary table name this repository manages."""
        pass

    def count(self, where_clause: str = "", params: tuple = ()) -> int:
        """
        Count rows in the repository's table.

        Args:
            where_clause: Optional WHERE clause (without 'WHERE' keyword)
            params: Parameters for the where clause

        Returns:
            Number of matching rows
        """
        sql = f"SELECT COUNT(*) as count FROM {self._table_name()}"
        if where_clause:
            sql += f" WHERE {where_clause}"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            result = cur.fetchone()
            return result['count'] if result else 0

    def exists(self, where_clause: str, params: tuple) -> bool:
        """
        Check if any rows match the criteria.

        Args:
            where_clause: WHERE clause (without 'WHERE' keyword)
            params: Parameters for the where clause

        Returns:
            True if at least one row exists
        """
        return self.count(where_clause, params) > 0

    def find_by_id(self, id_value: Any, id_column: str = "id") -> Optional[Dict[str, Any]]:
        """
        Find a single row by ID.

        Args:
            id_value: The ID value to search for
            id_column: Name of the ID column (default: "id")

        Returns:
            Dictionary representing the row, or None if not found
        """
        sql = f"SELECT * FROM {self._table_name()} WHERE {id_column} = ?"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (id_value,))
            return cur.fetchone()

    def find_all(self,
                 where_clause: str = "",
                 params: tuple = (),
                 order_by: str = "",
                 limit: Optional[int] = None,
                 offset: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Find all rows matching criteria.

        Args:
            where_clause: Optional WHERE clause
            params: Parameters for where clause
            order_by: Optional ORDER BY clause (e.g., "created_at DESC")
            limit: Optional maximum number of rows
            offset: Optional number of rows to skip

        Returns:
            List of dictionaries representing rows
        """
        sql = f"SELECT * FROM {self._table_name()}"

        if where_clause:
            sql += f" WHERE {where_clause}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()

    def delete_by_id(self, id_value: Any, id_column: str = "id") -> bool:
        """
        Delete a row by ID.

        Args:
            id_value: The ID value
            id_column: Name of the ID column

        Returns:
            True if a row was deleted
        """
        sql = f"DELETE FROM {self._table_name()} WHERE {id_column} = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (id_value,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted row with {id_column}={id_value} from {self._table_name()}")

        return deleted


class TransactionContext:
    """
    Context manager for database transactions.

    Usage:
        with TransactionContext(db_connection) as conn:
            repo1.insert(..., conn=conn)
            repo2.update(..., conn=conn)
            # Commits automatically if no exception
    """

    def __init__(self, db_connection: DatabaseConnection):
        self.db_connection = db_connection
        self.conn = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = sqlite3.connect(self.db_connection._db_path,
                                    timeout=10.0,
                                    check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = DatabaseConnection._dict_factory
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            try:
                self.conn.commit()
                logger.debug("Transaction committed successfully")
            except Exception as e:
                logger.error(f"Commit failed: {e}", exc_info=True)
                self.conn.rollback()
                raise
        else:
            logger.warning(f"Transaction rolled back due to: {exc_val}")
            self.conn.rollback()

        try:
            self.conn.close()
        except Exception:
            pass

        return False  # Re-raise exception if occurred
