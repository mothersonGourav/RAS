import re
import uuid
import json
from fastapi import APIRouter, Request, HTTPException
from app.schemas.pydantic_models import ChatRequest
from app.utils.logger import logger
from app.utils.helper_methods import format_ast_filters

router = APIRouter()

# Harmonia Endpoint
@router.post("/harmonia",summary="Send a natural language query")
async def chat(request_body: ChatRequest, request: Request):
    orchestrator = getattr(request.app.state, "harmonia_orchestrator", None)
    redis_client = getattr(request.app.state, "redis_client", None)
    logger.info(f"API received request for UserId -> {request_body.user_id}")
    if not orchestrator or not redis_client:
        raise HTTPException(status_code=503, detail="Required services not initialised yet.")
        
    session_id = getattr(request_body, "SessionId", None) or request_body.user_id
    user_query = request_body.message.strip()
    if hasattr(redis_client, 'get_chat_history'):
        chat_history = await redis_client.get_chat_history(request_body.user_id, session_id)
    else:
        chat_history = getattr(request_body, "History", [])
        
    state = {
            "current_query": request_body.message,
            "user_id": request_body.user_id,
            "user_role": request_body.user_role.lower().strip(),
            "session_id": session_id,
            "language": getattr(request_body, "Language", "en") or "en",
            "messages": chat_history,
            "query_type": request_body.query_type.lower().strip() if request_body.query_type else "sql",
    }
    
    try:
        result = await orchestrator.invoke(state)
    except Exception as e:
        logger.error(f"Orchestrator error for session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    
    #  Security Route
    if result.get("category") == "security_block":
        return {
            "response": result.get("final_response", "Security block triggered."),
            "response_type": "text", 
            "data": [], 
            "insights": [], 
            "filters_used": {}, 
            "sql_queries": []
        }
        
    #  Ambiguity Route
    if result.get("route") == "ambiguous":
        return {
            "response": result.get("final_response", "Multiple matches found. Please select one."),
            "response_type": "text",
            "data": [],
            "insights": [],
            "filters_used": {},
            "sql_queries": [],
            "is_ambiguous": True
        }

    final_text_response = result.get("final_response", "")
    
    # Append to chat history
    if hasattr(redis_client, 'append_chat_message'):
        await redis_client.append_chat_message(request_body.user_id, session_id, "user", request_body.message)
        await redis_client.append_chat_message(request_body.user_id, session_id, "assistant", final_text_response)
        
    #  Process Successful Response Datasets
    data = result.get("raw_db_results", [])
    record_count = len(data)
    sql_queries = result.get("sql_queries", [])
    verified_filters = result.get("filters_used", result.get("validated_schema", {}))
    display_filters = format_ast_filters(verified_filters)
    
    # Redis Cache 
    cache_id = str(uuid.uuid4())
    data_url = None
    if data:
        try:
            await redis_client.pool.setex(name=cache_id, time=3600, value=json.dumps(data))
            data_url = f"/api/data/{cache_id}"
        except Exception as e:
            logger.error(f"Failed to save data to Redis cache: {e}")
            cache_id = None

    # Method to check if the value is a numeric string for removing it from filters
    def is_numeric_string(val: str) -> bool:
        clean_val = re.sub(r'[><=!\s]', '', val)
        return clean_val.replace('.', '', 1).isdigit()

    final_display_filters = {}
    
    if display_filters:
        for key, value in display_filters.items():
            if isinstance(value, (int, float)):
                continue
            if isinstance(value, str) and is_numeric_string(value):
                continue
            if isinstance(value, list) and all(
                isinstance(v, (int, float)) or (isinstance(v, str) and is_numeric_string(v)) for v in value
            ):
                continue
            final_display_filters[key] = value

    return {
        "response": final_text_response,
        "response_type": result.get("response_type", "text" if record_count == 1 else "table"),
        "data":data,
        "cache_id": cache_id,
        "data_url": data_url,
        "insights": result.get("insights", []),
        "filters_used": final_display_filters,
        "sql_queries": [{"name": q.get("name", ""), "sql": q.get("sql", "")} for q in sql_queries],
        "graph_payload": result.get("graph_payload")
    }

# Cache Data Endpoint to retrieve the cached response returned from the Postgres DB.
@router.get("/api/data/{cache_id}", summary="Fetch cached raw chart data", status_code=200)
async def get_chart_data(cache_id: str, request: Request):
    redis_client = getattr(request.app.state, "redis_client", None)
    
    if not redis_client:
         raise HTTPException(status_code=503, detail="Cache service unavailable.")

    cached_data = await redis_client.pool.get(cache_id)
    if not cached_data:
        raise HTTPException(status_code=404, detail="Data has expired or does not exist")
        
    return {"data": json.loads(cached_data)}