from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.utils.config import Config
from app.clients.llms.openai_client import AzureOpenAIClient
from app.agents.query_rewriter_agent import QueryRewriterAgent
from app.agents.intent_agent import IntentAgent
from app.agents.sql_agent import SQLAgent
from app.agents.synthesizer_agent import SynthesizerAgent
from app.agents.guardrail_agent import GuardrailAgent
from app.tools.vector_search_tool import VectorSearchTool
from app.graph.nodes import GraphNodes
from app.graph.workflow import HybridSQLOrchestrator
from app.utils.schema_loader import SchemaManager
from app.utils.logger import logger
from app.clients.vector.azure_search_client import AzureSearchClient
from app.clients.rdbms.postgres_client import PostgresClient
from app.clients.cache.redis_client import RedisUserCache
from app.routes import ras_agent_router  

# Initialize lifespawn
@asynccontextmanager
async def lifespan(app: FastAPI):
    llm_client      = AzureOpenAIClient()
    schema_manager  = SchemaManager()
    vector_client   = AzureSearchClient(
                        endpoint   = Config.AZURE_SEARCH_ENDPOINT,
                        key        = Config.AZURE_SEARCH_KEY,
                        index_name = Config.AZURE_SEARCH_INDEX_NAME,)
    db_client       = PostgresClient()
    await db_client.connect()
    vector_tool     = VectorSearchTool(vector_client, db_client, schema_manager)
    query_rewriter  = QueryRewriterAgent(llm_client)
    intent_agent    = IntentAgent(llm_client, schema_manager)
    sql_agent       = SQLAgent(llm_client, db_client, schema_manager)
    synthesizer     = SynthesizerAgent(llm_client=llm_client)
    guardrail_agent = GuardrailAgent(llm_client)
    redis_cache = RedisUserCache()
    await redis_cache.connect()

    nodes = GraphNodes(
        guardrail_agent=guardrail_agent, query_rewriter=query_rewriter,
        intent_agent=intent_agent, sql_agent=sql_agent, synthesizer=synthesizer,
        vector_tool=vector_tool, db_client=db_client, redis_client=redis_cache
        )

    orchestrator = HybridSQLOrchestrator(nodes)
    
    # Store orchestrator in FastAPI's native application state
    app.state.orchestrator = orchestrator
    app.state.redis_client = redis_cache
    logger.info("RAS Agent API Started")
    yield
    await redis_cache.close()
    await db_client.close()
    logger.info("RAS Agent API Shutdown")

# Initialize FastAPI App
app = FastAPI(title="RAS SQL Agent API",
              version="1.0.0",
              lifespan=lifespan,
            )

# Connect the router
app.include_router(ras_agent_router.router)

# Health route
@app.get("/")
async def health():
    return {"status": "running", "service": "RAS Agent API"}