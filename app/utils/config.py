import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

class Config:

    # RAS CONFIG
    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY","")
    OPENAI_MODEL=os.getenv("OPENAI_MODEL","gpt-4.1")
    POSTGRES_CONFIG={"host":os.getenv("POSTGRES_HOST",""),"port":os.getenv("POSTGRES_PORT","5432"),"database":os.getenv("POSTGRES_DB",""),"user":os.getenv("POSTGRES_USER",""),"password":os.getenv("POSTGRES_PASSWORD","")}
    AZURE_SEARCH_ENDPOINT=os.getenv("AZURE_SEARCH_ENDPOINT","")
    AZURE_SEARCH_KEY=os.getenv("AZURE_SEARCH_KEY","")
    AZURE_SEARCH_INDEX_NAME=os.getenv("RAS_AZURE_SEARCH_INDEX_NAME","")
    AZURE_SEMANTIC_CONFIGURATION = "smr_semantic"
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
    SCHEMA_NAME="ras"
    DEFAULT_TABLE="tbl_ras_data"
    MAX_HISTORY=int(os.getenv("MAX_HISTORY",10))
    DEFAULT_TOP_N=int(os.getenv("DEFAULT_TOP_N",10))
    RAS_CHROMA_PATH    = r"C:\KUMAR PRINCE\SMR_USE_CASES\smr_ras_agent_\smr_ras_agent\app\chroma_db"
    CHROMA_COLLECTION = "few_shots_ras"
    LARGE_RESULT_THRESHOLD = 30
    EMBEDDING_MODEL = "text-embedding-3-small"
    AMBIGUITY_MARGIN = 0.15
    MIN_SCORE = 0.40
    FEW_SHOT_THRESHOLD = 0.70
    SIMILARITY_SCORE = 0.70
    REDIS_URL = "redis://localhost:6379/0"
    RAS_SCHEMA_FILE_PATH = r"C:\KUMAR PRINCE\SMR_USE_CASES\Data Mining Agent\app\metadata\ras_schema (1).json"
    ALLOWED_IPS : set = {

    }

    # Harmonia Config
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
    SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/Harmonia_DB.sqlite")
    POSTGRES_CONFIG={"host":os.getenv("POSTGRES_HOST",""),"port":os.getenv("POSTGRES_PORT","5432"),"database":os.getenv("POSTGRES_DB",""),"user":os.getenv("POSTGRES_USER",""),"password":os.getenv("POSTGRES_PASSWORD","")}
    AZURE_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    AZURE_KEY = os.getenv("AZURE_SEARCH_KEY", "")
    AZURE_INDEX = os.getenv("HARMONIA_AZURE_SEARCH_INDEX_NAME","")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")
    AZURE_SEMANTIC_CONFIGURATION = "smr_semantic"
    FEW_SHOT_THRESHOLD = 0.70
    AMBIGUITY_MARGIN = 0.02
    SIMILARITY_SCORE = 0.35
    if not OPENAI_API_KEY:
        raise ValueError(
            f"\n\nCRITICAL ERROR: OPENAI_API_KEY is completely missing!"
            f"\n1. We looked for the .env file here: {env_path}"
            f"\n2. Please ensure the file is named exactly '.env' (not '.env.txt')."
            f"\n3. Ensure it contains: OPENAI_API_KEY=sk-...\n\n"
        )
    HARMONIA_CHROMA_PATH  = r"C:\KUMAR PRINCE\SMR_USE_CASES\smr_harmonia_agent\app\Vector_DB\chroma-db-test"
    POSTGRES_URL = os.getenv("POSTGRES_URL")
    HARMONIA_SCHEMA_FILE_PATH = r"C:\KUMAR PRINCE\SMR_USE_CASES\Data Mining Agent\app\metadata\harmonia_old.json"
    COLLECTION_NAME = "employee-master-collection"
    FEW_SHOTS_COLLECTION_NAME = "sql_few_shot_examples"
    REDIS_URL = "redis://localhost:6379/0"
    EMBEDDING_MODEL = "text-embedding-3-small"
    HISTORY_LIMIT = 50