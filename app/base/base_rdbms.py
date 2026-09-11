from abc import ABC, abstractmethod
import pandas as pd


class BaseRDBMS(ABC):
    """Interface for all relational databases (PostgreSQL, SQLite, SQL Server, etc.)"""

    @abstractmethod
    async def get_schema(self) -> str:
        """Returns database schema as a formatted string."""
        pass

    @abstractmethod
    async def execute_query(self, query: str) -> list[dict]:
        """Executes SQL query and returns rows as list of dictionaries."""
        pass

    @abstractmethod
    async def get_dataframe(self, query: str) -> pd.DataFrame:
        """Executes SQL query and returns result as pandas DataFrame."""
        pass