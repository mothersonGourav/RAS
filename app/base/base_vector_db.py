from abc import ABC, abstractmethod


class BaseVectorDB(ABC):
    """Interface for all Vector databases (Azure Search, ChromaDB, Pinecone)"""

    @abstractmethod
    async def search(self, query_text: str, top_k: int = 50) -> list[dict]:
        """Returns a list of matching document dictionaries."""
        pass