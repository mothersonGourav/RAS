from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.utils.config import Config
from app.utils.logger import logger

# Shared Clients
from app.clients.cache.redis_client import RedisUserCache
from app.clients.rdbms.postgres_client import PostgresClient
from app.clients.vector.azure_search_client import AzureSearchClient
from app.utils.schema_loader import HarmoniaSchemaManager,RASSchemaManager
from app.clients.llms.openai_client import AzureOpenAIClient 

# Orchestrators and Nodes
from app.graph.workflow import HarmoniaSQLOrchestrator,RASSQLOrchestrator
from app.graph.nodes import HarmoniaGraphNodes,RASGraphNodes
from app.agents.query_rewriter_agent import HarmoniaQueryRewriterAgent,RASQueryRewriterAgent
from app.agents.intent_agent import HarmoniaIntentAgent,RASIntentAgent
from app.agents.sql_agent import HarmoniaSQLAgent,RASSQLAgent
from app.agents.synthesizer_agent import HarmoniaSynthesizerAgent,RASSynthesizerAgent
from app.agents.guardrail_agent import GuardrailAgent
from app.tools.vector_search_tool import HarmoniaVectorSearchTool,RASVectorSearchTool
from app.agents.synthesizer_agent import HarmoniaSynthesizerAgent,RASSynthesizerAgent
from app.tools.pandas_analyzer_tool import PandasAnalyzerTool

# Routers
from app.routes import harmonia_agent_router, ras_agent_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API: Initialising shared infrastructure.")
    
    # 1. Initialize Shared Infrastructure (Singletons)
    schema_manager_ras = RASSchemaManager()
    schema_manager_harmonia = HarmoniaSchemaManager()
    db_client = PostgresClient()
    await db_client.connect()
    
    redis_client = RedisUserCache()
    await redis_client.connect()
    
    vector_client = AzureSearchClient(
        endpoint=Config.AZURE_SEARCH_ENDPOINT,
        key=Config.AZURE_SEARCH_KEY,
        index_name=Config.AZURE_SEARCH_INDEX_NAME,
    )
    
    # Initialize the Singleton Azure OpenAI Client
    azure_llm_client = AzureOpenAIClient()

    # 2. Build Harmonia Dependency Graph
    harmonia_nodes = HarmoniaGraphNodes(
        query_rewriter=HarmoniaQueryRewriterAgent(azure_llm_client),
        intent_agent=HarmoniaIntentAgent(azure_llm_client, schema_manager_harmonia),
        sql_agent=HarmoniaSQLAgent(azure_llm_client, db_client, schema_manager_harmonia),
        synthesizer=HarmoniaSynthesizerAgent(llm_client=azure_llm_client, redis_client=redis_client),
        vector_tool=HarmoniaVectorSearchTool(vector_client, db_client, schema_manager_harmonia),
        pandas_tool=PandasAnalyzerTool(), 
        db_client=db_client,
        guardrail_agent=GuardrailAgent(azure_llm_client),
        redis_client=redis_client
    )
    
    # 3. Build RAS Dependency Graph
    ras_nodes = RASGraphNodes(
        guardrail_agent=GuardrailAgent(azure_llm_client),
        query_rewriter=RASQueryRewriterAgent(azure_llm_client),
        intent_agent=RASIntentAgent(azure_llm_client, schema_manager_ras),
        sql_agent=RASSQLAgent(azure_llm_client, db_client, schema_manager_ras),
        synthesizer=RASSynthesizerAgent(llm_client=azure_llm_client),
        vector_tool=RASVectorSearchTool(vector_client, db_client, schema_manager_ras),
        db_client=db_client,
        redis_client=redis_client
    )

    # 4. Attach to State Namespaces
    app.state.harmonia_orchestrator = HarmoniaSQLOrchestrator(harmonia_nodes)
    app.state.ras_orchestrator = RASSQLOrchestrator(ras_nodes)
    app.state.db_client = db_client
    app.state.redis_client = redis_client
    
    logger.info("API: Multi-Agent graphs ready.")
    yield
    
    # Graceful Shutdown
    await redis_client.pool.aclose()
    await db_client.close()
    await vector_client.close()
    logger.info("API: Shutdown complete.")

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(harmonia_agent_router.router, prefix="/api/v1/harmonia", tags=["Harmonia"])
app.include_router(ras_agent_router.router, prefix="/api/v1/ras", tags=["RAS"])