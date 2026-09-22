from abc import ABC, abstractmethod
import pandas as pd


class BaseRDBMS(ABC):
    """Interface for all relational databases (SQLite, PostgreSQL, etc.)"""

    @abstractmethod
    async def get_schema(self) -> str:
        """Returns the database schema as a string."""
        pass

    @abstractmethod
    async def execute_query(self, query: str) -> list[dict]:
        """Executes a SQL query and returns rows as dictionaries."""
        pass

    @abstractmethod
    async def get_dataframe(self, query: str) -> pd.DataFrame:
        """Executes a SQL query and returns a Pandas DataFrame."""
        pass

