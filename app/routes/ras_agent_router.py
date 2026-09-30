import re
import uuid
from fastapi import APIRouter, Request, HTTPException
from app.utils.logger import logger
from app.utils.config import Config
from app.models.pydantic_models import ChatRequest, QueryResponse
from app.utils.helper_methods import _extract_implicit_filters, format_ast_filters
from typing import Dict, Any

router = APIRouter()

# Endpoint to chat with Ras DB
@router.post("/ras", response_model=QueryResponse)
async def chat(request: Request, payload: ChatRequest): 
    orchestrator = request.app.state.orchestrator
    redis_client = request.app.state.redis_client
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Agent Orchestrator not initialized.")

    logger.info(f"API received request for UserId -> {payload.UserId}")

    request_id = str(uuid.uuid4())
    lock_acquired = await redis_client.acquire_lock(payload.UserId, request_id, timeout=25)
    if not lock_acquired:
        return QueryResponse(
            response="Your previous query is still processing. Please wait.",
            response_type="text", data=[], insights=[], filters_used={}, sql_queries=[]
        )
        
    try: 
        chat_history = await redis_client.get_chat_history(payload.UserId)
        semantic_state = await redis_client.get_semantic_state(payload.UserId)
        pending_state = await redis_client.get_semantic_state(f"pending_{payload.UserId}")

        result = await orchestrator.invoke({
            "current_query": payload.Request,
            "user_id": payload.UserId,
            "language": getattr(payload, "Language", "en") or "en",
            "messages": chat_history,
            "semantic_state": semantic_state,
            "pending_semantic_state": pending_state
        })
        
        # Security Route
        if result.get("category") == "security_block":
            sec_response = QueryResponse(
                response=result.get("final_response", "Security block triggered."),
                response_type="text", data=[], insights=[], filters_used={}, sql_queries=[]
            )
            if hasattr(redis_client, 'append_ui_history'):
                ui_payload = sec_response.model_dump()
                ui_payload["request"] = payload.Request
                await redis_client.append_ui_history(payload.UserId, ui_payload)
            return sec_response

        # Ambiguity Route
        if result.get("route") == "ambiguous":
            amb_response = QueryResponse(
                response=result.get("final_response", "Multiple matches found. Please select one."),
                response_type="text", data=[], insights=[], filters_used={}, sql_queries=[],
                is_ambiguous=True
            )
            if hasattr(redis_client, 'append_ui_history'):
                ui_payload = amb_response.model_dump()
                ui_payload["request"] = payload.Request
                await redis_client.append_ui_history(payload.UserId, ui_payload)
            return amb_response

        # Successful LLM Conversational Track
        final_text_response = result.get("final_response", "")
        if hasattr(redis_client, 'append_chat_message'):
            await redis_client.append_chat_message(payload.UserId, "user", payload.Request)
            await redis_client.append_chat_message(payload.UserId, "assistant", final_text_response)

        # Successful Response Processing
        data         = result.get("raw_db_results", [])
        record_count = result.get("record_count", len(data))
        sql_queries  = result.get("sql_queries", [])
        verified_filters = result.get("filters_used", result.get("validated_schema", {}))
        display_filters = format_ast_filters(verified_filters)

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
                
        query_response = QueryResponse(
            response=result.get("final_response", ""),
            response_type="text" if record_count == 1 else "table",
            data=data,
            insights=result.get("insights", []),
            filters_used=final_display_filters,
            sql_queries=[{"name": q.get("name", ""), "sql": q.get("sql", "")} for q in sql_queries]
        )
        
        if hasattr(redis_client, 'append_ui_history'):
            ui_payload = query_response.model_dump()
            ui_payload["request"] = payload.Request 
            await redis_client.append_ui_history(payload.UserId, ui_payload)

        return query_response

    except Exception as e:
        logger.error(f"API Error: {str(e)}", exc_info=True)
        return QueryResponse(response="Internal server error.", response_type="text")
        
    finally:
        await redis_client.release_lock(payload.UserId, request_id)

# Endpoint for finding records using requisition number 
@router.post("/ras/requisition-number", response_model=QueryResponse)
async def fetch_requisitions(request: Request, payload: ChatRequest):
    try:
        orchestrator = request.app.state.orchestrator
        redis_client = request.app.state.redis_client
        
        if not orchestrator:
            raise HTTPException(status_code=500, detail="Agent Orchestrator not initialized.")
        
        db_client = orchestrator.nodes.db_client
        synthesizer = orchestrator.nodes.synthesizer
        req_no = payload.Request.strip()

        cache_query_string = f"req_lookup:{req_no}_{payload.UserId}_{payload.Scope}"

        if hasattr(redis_client, 'get_cached_response'):
            cached_result = await redis_client.get_cached_response(
                user_id=payload.UserId,
                language=payload.Language, 
                query=cache_query_string
            )
            
            if cached_result:
                if hasattr(redis_client, 'append_chat_message'):
                    await redis_client.append_chat_message(payload.UserId, "user", payload.Request)
                    await redis_client.append_chat_message(payload.UserId, "assistant", cached_result.get("final_response", ""))
                
                cached_data = cached_result.get("raw_db_results", [])
                threshold = getattr(Config, "LARGE_RESULT_THRESHOLD", 30)
                is_large = len(cached_data) > threshold

                cached_query_response = QueryResponse(
                    response=cached_result.get("final_response", ""),
                    response_type="text" if is_large else "table",
                    data=cached_data,
                    insights=cached_result.get("insights", []),
                    filters_used={"requisition_number": req_no},
                    sql_queries=[{"name": "Cached Lookup", "sql": "Retrieved from Redis Cache"}]
                )
                
                if hasattr(redis_client, 'append_ui_history'):
                    ui_payload = cached_query_response.model_dump()
                    ui_payload["request"] = payload.Request
                    await redis_client.append_ui_history(payload.UserId, ui_payload)
                    
                return cached_query_response

        schema = getattr(Config, "SCHEMA_NAME", "ras")
        table  = getattr(Config, "DEFAULT_TABLE", "tbl_ras_data")
        target_table = f"{schema}.{table}"
        sql_query = f"SELECT * FROM {target_table} WHERE requisition_number = $1"
        raw_db_results = await db_client.execute_query(sql_query, req_no)

        mock_state = {
            "current_query": payload.Request,
            "query_result": raw_db_results,
            "raw_db_results": raw_db_results,
            "raw_filters": {"requisition_number": req_no},
            "metric": None
        }

        synth_result = await synthesizer(mock_state)
        final_text_response = synth_result.get("final_response", "")
        final_data = synth_result.get("data", [])

        if hasattr(redis_client, 'save_response'):
            await redis_client.save_response(
                user_id=payload.UserId,
                language=payload.Language,
                query=cache_query_string,
                final_response=final_text_response,
                data=final_data
            )
        
        if hasattr(redis_client, 'append_chat_message'):
            await redis_client.append_chat_message(payload.UserId, "user", payload.Request)
            await redis_client.append_chat_message(payload.UserId, "assistant", final_text_response)

        query_response = QueryResponse(
            response=final_text_response,
            response_type="text" if synth_result.get("is_large_result") else "table",
            data=final_data,
            insights=synth_result.get("insights", []),
            filters_used={"requisition_number": req_no},
            sql_queries=[{"name": "REQUISITION ENDPOINT", "sql": sql_query}]
        )

        if hasattr(redis_client, 'append_ui_history'):
            ui_payload = query_response.model_dump()
            ui_payload["request"] = payload.Request
            await redis_client.append_ui_history(payload.UserId, ui_payload)
            
        return query_response

    except ValueError as ve:
        logger.warning(f"Validation error in /requisition: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Requisition Endpoint Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error fetching requisition data.")

# Endpoint for finding the history
@router.get("/api/history/{user_id}", summary="Fetch user chat history", status_code=200)
async def get_user_history(user_id: str, request: Request, limit: int = 50) -> Dict[str, Any]:
    redis_client = getattr(request.app.state, "redis_client", None)
    if not redis_client:
        raise HTTPException(status_code=503, detail="Cache service unavailable.")
    try:
        history = await redis_client.get_ui_history(user_id=user_id, limit=limit)      
        return {"user_id": user_id, "history": history}
    except Exception as e:
        logger.error(f"History Fetch Error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching history.")