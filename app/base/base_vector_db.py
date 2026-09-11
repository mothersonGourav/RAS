from abc import ABC, abstractmethod


class BaseVectorDB(ABC):
    """Interface for all Vector databases (Azure Search, ChromaDB, Pinecone)."""

    @abstractmethod
    async def search(self,query_text: str,top_k: int = 5,query_type: str = "semantic",) -> list[dict]:
        """
        Returns a list of matching document dictionaries.
        
        query_type:
          - 'semantic' : vector/semantic search (default)
          - 'full'     : Azure full Lucene syntax — enables ~ fuzzy operator
                         e.g. 'sueyoung~' will match 'sooyoung' in the index
        """
        pass