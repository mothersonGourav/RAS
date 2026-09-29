from typing import List, Dict, Any
import base64
import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from langchain_openai import AzureOpenAIEmbeddings
from app.base.base_vector_db import BaseVectorDB
from app.utils.config import Config
from app.utils.logger import logger


class AzureSearchClient(BaseVectorDB):

    """Handles semantic querying against the local Azure AI Search vector space."""

    def __init__(self, endpoint: str = None, key: str = None, index_name: str = None):
        self.endpoint   = endpoint   or Config.AZURE_SEARCH_ENDPOINT
        self.index_name = Config.AZURE_SEARCH_INDEX_NAME
        self.credential = AzureKeyCredential(key or Config.AZURE_SEARCH_KEY)
        self.embedder = AzureOpenAIEmbeddings(
            azure_deployment="text-embedding-3-small11", 
            azure_endpoint=os.getenv("AZURE_EMBEDDER_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version= "2023-05-15"
            )
        self.client     = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=self.credential,
        )

    # Extract and Normalize the search score
    def normalize_score(self, doc: dict) -> float:
        reranker_score = doc.get("@search.reranker_score")
        if reranker_score is not None:
            return min(float(reranker_score) / 4.0, 1.0)
        raw_score = doc.get("@search.score", 0.0)
        return min(float(raw_score), 1.0)

    # Search method for searching Azure AI Search using Hybrid/Semantic Vector Search.
    async def search(
        self,
        query_text: str,
        target_table: str = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:

        logger.info(f"Azure Search: Performing semantic search for '{query_text}'")
        try:
            azure_system_keys = {
                "content",
                "content_vector",
                "@search.score",
                "@search.reranker_score",
                "@search.highlights",
                "@search.captions",
            }

            odata_filter = f"table_name eq '{target_table}'" if target_table else None
            
            # Embed the query
            query_vector = await self.embedder.aembed_query(query_text)

            vector_query = VectorizedQuery(
                vector=query_vector,
                k_nearest_neighbors=top_k,
                fields="content_vector",
            )
            raw_results_async = await self.client.search(
                search_text=query_text,
                vector_queries=[vector_query],
                filter=odata_filter,
                # query_type="semantic",
                top=top_k,
                # semantic_configuration_name=Config.AZURE_SEMANTIC_CONFIGURATION
            )
            raw_results = [doc async for doc in raw_results_async]

            standardized_results = []

            for doc in raw_results:
                normalized_score = self.normalize_score(doc)
                raw_id   = doc.get("id", "")
                clean_id = raw_id
                
                if isinstance(raw_id, str) and len(raw_id) % 4 == 0:
                    try:
                        decoded = base64.b64decode(raw_id).decode("utf-8")
                        if any(c.isdigit() for c in decoded):
                            clean_id = decoded
                    except Exception:
                        pass
                        
                metadata = {k: v for k, v in doc.items() if k not in azure_system_keys}
                
                standardized_results.append({
                    "id":       clean_id,
                    "score":    normalized_score,
                    "content":  doc.get("content", ""),
                    "metadata": metadata,
                })
            standardized_results.sort(key=lambda x: x["score"], reverse=True)

            logger.info(f"Azure Search: Retrieved {len(standardized_results)} results.")
            return standardized_results

        except Exception as e:
            logger.error(f"Azure Search Error: {str(e)}", exc_info=True)
            return []