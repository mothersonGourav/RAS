from dotenv import load_dotenv
import os


load_dotenv()


class Config:
    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY","")
    OPENAI_MODEL=os.getenv("OPENAI_MODEL","gpt-4.1")
    POSTGRES_CONFIG={"host":os.getenv("POSTGRES_HOST",""),"port":os.getenv("POSTGRES_PORT","5432"),"database":os.getenv("POSTGRES_DB",""),"user":os.getenv("POSTGRES_USER",""),"password":os.getenv("POSTGRES_PASSWORD","")}
    AZURE_SEARCH_ENDPOINT=os.getenv("AZURE_SEARCH_ENDPOINT","")
    AZURE_SEARCH_KEY=os.getenv("AZURE_SEARCH_KEY","")
    AZURE_SEARCH_INDEX_NAME=os.getenv("AZURE_SEARCH_INDEX_NAME","")
    AZURE_SEMANTIC_CONFIGURATION = "smr_semantic"
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
    SCHEMA_NAME="ras"
    DEFAULT_TABLE="tbl_ras_data"
    MAX_HISTORY=int(os.getenv("MAX_HISTORY",10))
    DEFAULT_TOP_N=int(os.getenv("DEFAULT_TOP_N",10))
    CHROMA_DB_PATH    = r"C:\KUMAR PRINCE\SMR_USE_CASES\smr_ras_agent_\smr_ras_agent\app\chroma_db"
    CHROMA_COLLECTION = "few_shots_ras"
    LARGE_RESULT_THRESHOLD = 30
    EMBEDDING_MODEL = "text-embedding-3-small"
    AMBIGUITY_MARGIN = 0.15
    MIN_SCORE = 0.40
    FEW_SHOT_THRESHOLD = 0.70
    SIMILARITY_SCORE = 0.70
    REDIS_URL = "redis://localhost:6379/0"
    SCHEMA_FILE_PATH = r"C:\KUMAR PRINCE\SMR_USE_CASES\smr_ras_agent_\smr_ras_agent\app\metadata\ras_schema (1).json"
    APP_API_KEY :str = os.getenv("APP_API_KEY","")
    ALLOWED_IPS : set = {

    }