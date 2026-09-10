"""
INTERNAL - shared DB utilities (connect_to_db, archive helpers); imported by every slice. Not run directly.

Analytics database utilities for color analysis pipeline.

Provides lightweight database connection and archive management functions
for the athletic color analytics repository.
"""

import psycopg2
from psycopg2 import sql
import os
from datetime import datetime
from psycopg2.extras import DictCursor


def connect_to_db(brand: str, readonly: bool = True, db_user: str = None):
    """
    Establish a PostgreSQL connection to the specified brand database.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas', 'puma', 'lulu', 'ua')
        readonly: If True (default), sets the session to TRANSACTION READ ONLY.
            Any INSERT/UPDATE/DELETE on the returned connection then errors at
            the driver ("cannot execute … in a read-only transaction") — a hard
            guard, not a hope. Writer call sites must pass readonly=False
            explicitly; every other caller gets driver-layer write protection
            for free.
        db_user: If set, connects as this Postgres role instead of 'postgres'.
            Combined with a role that only holds SELECT on the read-only tables
            (archive/instance/family/appendix), this makes accidental writes to
            those tables physically impossible even without readonly=True.

    Returns:
        Tuple of (connection, cursor) with DictCursor factory

    Environment Variables:
        POSTGRES_PASSWORD: Required - Database password (used for whichever
            role is chosen via db_user; sandbox roles are provisioned with the
            same password so no separate env var is needed).
        DB_HOST: Optional - Database host (default: 'localhost')
        DB_PORT: Optional - Database port (default: 5432)
    """
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise ValueError("POSTGRES_PASSWORD environment variable must be set")

    db_params = {
        'dbname': brand,
        'user': db_user or 'postgres',
        'password': password,
        'host': os.getenv("DB_HOST", "localhost"),
        'port': int(os.getenv("DB_PORT", "5432")),
    }

    conn = psycopg2.connect(**db_params)
    if readonly:
        conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=DictCursor)
    return conn, cur


def create_subfolder_datetimesuffix(parent_folder_name: str, new_subfolder_name: str) -> str:
    """
    Create a timestamped subfolder for analytics output.

    Args:
        parent_folder_name: Parent folder path
        new_subfolder_name: Base name for new subfolder

    Returns:
        Full path to created subfolder

    Example:
        create_subfolder_datetimesuffix('./data', 'segmentation')
        -> './data/segmentation_2025-01-10_14-30-45'
    """
    # Get the current date and time in the desired format
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    # Add the timestamp suffix to the folder name
    prefixed_subfolder_name = f"{new_subfolder_name}_{timestamp}"

    # Ensure the parent folder exists
    parent_folder_path = os.path.join(os.getcwd(), parent_folder_name)
    os.makedirs(parent_folder_path, exist_ok=True)

    # Create the new subfolder with the timestamped name
    new_subfolder_path = os.path.join(parent_folder_path, prefixed_subfolder_name)
    os.makedirs(new_subfolder_path, exist_ok=True)

    return new_subfolder_path


def get_unprocessed_archives(cur, dest_table: str) -> list:
    """
    Find archive IDs that haven't been processed by the specified analytics table.

    Args:
        cur: Database cursor
        dest_table: Name of analytics destination table (e.g., 'segment_fpyolo11l241114')

    Returns:
        List of unprocessed archive IDs sorted in ascending order

    Example:
        unprocessed = get_unprocessed_archives(cursor, 'segment_fpyolo11l241114')
        # Returns: [5, 7, 12] (archives not yet in segmentation table)
    """
    query = sql.SQL("""
        SELECT archive_id
            FROM archive
        EXCEPT
        SELECT DISTINCT(archive_id_ref)
            FROM {table}
        ORDER BY archive_id
    """).format(
        table=sql.Identifier(dest_table)
    )

    cur.execute(query)
    rows = cur.fetchall()
    return [r[0] for r in rows]


def map_archive_dirs(brand: str, search_dir: str) -> dict:
    """
    Map archive IDs to their corresponding folder paths by parsing folder names.

    Searches for folders matching the pattern: BRAND_query_archiveID_timestamp

    Args:
        brand: Brand name to filter folders (e.g., 'nike', 'adidas')
        search_dir: Directory to search for archive folders

    Returns:
        Dictionary mapping archive_id (str) -> folder_path (str)

    Example:
        dirs = map_archive_dirs('nike', '.')
        # Returns: {'123': './NIKE_running-shoes_123_2025-01-10_14-30-45', ...}
    """
    archive_dir_map = {}
    brand_lower = brand.lower()

    for folder in os.listdir(search_dir):
        folder_path = os.path.join(search_dir, folder)
        if os.path.isdir(folder_path):
            parts = folder.split('_')
            if len(parts) >= 4:
                folder_brand = parts[0].lower()
                archive_id_part = parts[2]
                if folder_brand == brand_lower:
                    archive_dir_map[archive_id_part] = folder_path

    return archive_dir_map