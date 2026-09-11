import chromadb
from langchain_openai import OpenAIEmbeddings,AzureOpenAIEmbeddings
from app.utils.logger import logger
from app.utils.config import Config
import os


class ChromaSearchClient:
    """Handles semantic querying against the local ChromaDB vector space."""

    def __init__(self, db_dir: str, collection_name: str, api_key: str):
        logger.info(f"Connecting to ChromaDB at {db_dir}...")
        self.chroma_client = chromadb.PersistentClient(path=db_dir)
        self.collection    = self.chroma_client.get_collection(name=collection_name)
        self.embedder = AzureOpenAIEmbeddings(
            azure_deployment="text-embedding-3-small11", 
            azure_endpoint=os.getenv("AZURE_EMBEDDER_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version= "2023-05-15"
            )
    # Vector search on key:value provided in the filter from intent agent."
    async def search(self,query_text: str,target_table: str = None,top_k: int = 5) -> list[dict]:

        where_filter = {"TableName": target_table} if target_table else None
        raw_results  = self.collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where=where_filter
        )

        if not raw_results or not raw_results.get("ids") or not raw_results["ids"][0]:
            return []

        standardized_results = []
        distances  = raw_results.get("distances", [[0] * top_k])[0]
        docs       = raw_results.get("documents",  [[""] * top_k])[0]
        metas      = raw_results.get("metadatas",  [[{}] * top_k])[0]
        chroma_system_keys = {"content", "content_vector", "table_name", "DocId"}

        for i in range(len(raw_results["ids"][0])):
            distance       = distances[i] if distances is not None else 1.0
            semantic_score = max(1.0 - distance, 0.0)
            pure_meta      = {k: v for k, v in metas[i].items() if k not in chroma_system_keys}

            standardized_results.append({
                "id":       raw_results["ids"][0][i],
                "score":    semantic_score,
                "content":  docs[i],
                "metadata": pure_meta
            })

        standardized_results.sort(key=lambda x: x["score"], reverse=True)
        return standardized_results

    # Semantic search for few-shot examples using OpenAI embeddings.
    async def search_few_shots(self,query_text: str,top_k: int = 3) -> list[dict]:
 
        if not query_text.strip():
            logger.warning("[FewShot] Empty query — skipping ChromaDB search.")
            return []

        try:
            query_embedding = self.embedder.embed_query(query_text)

            raw_results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, self.collection.count())
            )

            if not raw_results or not raw_results.get("ids") or not raw_results["ids"][0]:
                logger.warning("[FewShot] No results returned from ChromaDB.")
                return []

            examples  = []
            distances = raw_results.get("distances", [[]])[0]
            metas     = raw_results.get("metadatas",  [[]])[0]

            for meta, dist in zip(metas, distances):
                similarity = round(max(1.0 - dist, 0.0), 4)
                examples.append({
                    "question":   meta.get("question", ""),
                    "sql":        meta.get("sql", ""),
                    "similarity": similarity
                })

            logger.info(
                f"[FewShot] Retrieved {len(examples)} examples | "
                f"Similarities: {[ex['similarity'] for ex in examples]}"
            )
            return examples

        except Exception as e:
            logger.error(f"[FewShot] ChromaDB search error: {e}", exc_info=True)
            return []