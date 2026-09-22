import sqlite3
import pandas as pd
import asyncio
from app.base.base_rdbms import BaseRDBMS
from app.core.exceptions import SQLValidationError
from app.utils.logger import logger


class SQLiteClient(BaseRDBMS):
    """Concrete implementation for SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # Synchronous execution function to be run in a separate thread
    def _execute_sync(self, query: str) -> list[dict]:
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            raise SQLValidationError(f"Database rejected the query: {str(e)}")

    # Executes the query without blocking the main event loop
    async def execute_query(self, query: str) -> list[dict]:

        return await asyncio.to_thread(self._execute_sync, query)

    # Converts query results into a Pandas DataFrame
    async def get_dataframe(self, query: str) -> pd.DataFrame:

        results = await self.execute_query(query)
        return pd.DataFrame(results)

    # Fetches the DB schema
    async def get_schema(self) -> str:

        query = "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        results = await self.execute_query(query)
        return "\n".join([row['sql'] for row in results if row['sql']])
    
    # Returns a map of {column_name: table_name} required by the VectorSearchTool
    async def get_schema_cache(self) -> dict:

        logger.info("Building SQLite dynamic schema cache...")
        cache = {}
        tables = await self.execute_query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        for t in tables:
            table_name = t['name']
            columns = await self.execute_query(f"PRAGMA table_info({table_name});")
            for col in columns:
                cache[col['name'].lower()] = table_name
                
        return cache