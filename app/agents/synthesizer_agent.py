
import json
import uuid
import pandas as pd
from app.schemas.pydantic_models import DashboardResponse
from app.prompts.prompt_registry import RAS_SYNTHESIZER_SYSTEM_PROMPT,HARMONIA_SYNTHESIZER_SYSTEM_PROMPT
from app.utils.logger import logger
from app.utils.html_util import dataframe_to_compact_html
from app.utils.helper_methods import clean_filters_for_display, generate_summary
from app.utils.config import Config

LARGE_RESULT_THRESHOLD = Config.LARGE_RESULT_THRESHOLD

class HarmoniaSynthesizerAgent:
    def __init__(self, llm_client, redis_client):
        self.llm = llm_client
        self.redis = redis_client 

    async def __call__(self, state: dict) -> dict:
        raw_db_results = state.get("raw_db_results", [])
        query_type = state.get("query_type", "sql").lower()
        current_query = state.get("current_query", "")
        user_id = state.get("user_id", "system")
        language = state.get("language", "english")
        
        # 1. Handle Empty Results
        if not raw_db_results:
            return {
                "response_type": "text",
                "final_response": "I successfully ran the query, but no records were found.",
                "data": [],
                "dashboard_payload": None,
                "cache_id": None
            }

        # 2. Write Raw Data to Cache (Applies to both SQL and Graph routes)
        cache_id = str(uuid.uuid4())
        try:
            await self.redis.set_cache(
                user_id=user_id,
                module="HarmoniaData",   
                topic="RawQueryData",    
                language=language,
                query=cache_id,          
                data=raw_db_results      
            )
            logger.info(f"Synthesizer: Raw data cached successfully with ID: {cache_id}")
        except Exception as e:
            logger.error(f"Synthesizer Cache Error: Failed to write to Redis - {e}")
        # PATH 1 - SQL ROUTE
        if query_type == "sql":
            logger.info("Synthesizer: Executing SQL Route...")
            
            # Use lightweight text generator for the summary
            text_summary = await generate_summary(
                llm=self.llm,
                user_query=current_query,
                record_count=len(raw_db_results),
                data=raw_db_results
            )
            
            return {
                "response_type": "table",               
                "final_response": text_summary,         
                "data": raw_db_results,
                "dashboard_payload": None,
                "cache_id": cache_id
            }
        # PATH 2 - GRAPH ROUTE
        if query_type == "graph":
            logger.info("Synthesizer: Executing Graph Route (30-row sample)...")
            
            # Sample exactly 30 rows for the LLM to decide the graph type
            sampled_data = raw_db_results[:30] 
            
            user_prompt = (
                f"USER QUERY: {current_query}\n\n"
                f"DATA SAMPLE (Top {len(sampled_data)} rows):\n"
                f"{json.dumps(sampled_data, indent=2)}\n\n"
                "Analyze the schema of this sample. Determine the best chart type, map the axes, "
                "extract KPIs, and generate a brief summary of the insights."
            )
            
            try:
                # LLM decides graph type, KPIs, and insights
                response: DashboardResponse = await self.llm.generate_structured(
                    system_prompt=HARMONIA_SYNTHESIZER_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema_class=DashboardResponse
                )
                
                return {
                    "response_type": "dashboard" if response.is_dashboard else "table",
                    "final_response": response.text_response, 
                    "data": [], 
                    "cache_id": cache_id, 
                    "dashboard_payload": {
                        "chart_config": response.chart_config.model_dump() if response.chart_config else None,
                        "kpis": [kpi.model_dump() for kpi in response.kpis] if response.kpis else []
                    }
                }

            except Exception as e:
                logger.error(f"Synthesizer Graph Error: {e}", exc_info=True)
                return {
                    "response_type": "table",
                    "final_response": "Here is the data. (Encountered an error while configuring the graph).",
                    "data": raw_db_results,
                    "dashboard_payload": None,
                    "cache_id": cache_id
                }

class RASSynthesizerAgent:
    def __init__(self, llm_client):
        self.llm = llm_client

    # Generates the response sentence, insights - Table HTML for less than 30 records.
    async def build_small_result_response(
        self,
        state: dict,
        data: list,
        record_count: int,
        display_filters: dict,
    ) -> dict:
        
        user_query  = state.get("current_query", "Results")
        metric      = state.get("metric")
        filter_text = (
            ", ".join([f"{k}: {v}" for k, v in display_filters.items()])
            if display_filters else "None"
        )

        data_text = "\n".join([str(row) for row in data])
        try:
            response = await self.llm.generate_text(
                system_prompt=(
                    "You are a data analyst. "
                    "Return ONLY a JSON object with two keys: "
                    "'response' (one plain-text summary sentence) and "
                    "'insights' (a JSON array of 1-3 plain-text insight strings). "
                    "No HTML, no markdown, no code fences."
                ),
                user_prompt=(
                    f"User asked: {user_query}\n"
                    f"RECORD COUNT: {record_count}\n"
                    f"METRIC: {metric or 'N/A'}\n"
                    f"FILTERS: {filter_text}\n\n"
                    f"DATA:\n{data_text}\n\n"
                    f"Return JSON with:\n"
                    f"- 'response': max 50-word factual sentence using the entire data provided (e.g. 'SMR Hungary spent 62M EUR over 3 fiscal years.')\n"
                    f"- 'insights': array of 1-3 short strings, each max 30 words."
                ),
            )

            raw = response.strip() if isinstance(response, str) else ""
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            parsed   = json.loads(raw)
            summary  = parsed.get("response", "").strip()
            insights = parsed.get("insights", [])

            if not summary:
                summary = f"Successfully retrieved {record_count} record{'s' if record_count != 1 else ''}."
            if not isinstance(insights, list):
                insights = []

            return {"final_response": summary, "insights": insights}

        except Exception as e:
            logger.error(f"Small result LLM error: {e}", exc_info=True)
            return {
                "final_response": f"Successfully retrieved {record_count} record{'s' if record_count != 1 else ''}.",
                "insights":       [],
            }
        
    async def __call__(self, state: dict) -> dict:
        query_result   = state.get("query_result")
        raw_results    = state.get("raw_db_results", [])
        filters_used   = state.get("validated_schema", state.get("raw_filters", {}))

        if isinstance(query_result, list):
            df = pd.DataFrame(query_result)
        elif isinstance(query_result, pd.DataFrame):
            df = query_result
        else:
            df = pd.DataFrame(raw_results)

        if df.empty:
            return {
                "final_response":  "No matching records found.",
                "response_html":   "<div><h2>No matching records found.</h2></div>",
                "record_count":    0,
                "data":            [],
                "insights":        [],
                "is_large_result": False,
            }

        record_count    = len(df)
        user_query      = state.get("current_query", "Results")
        display_filters = clean_filters_for_display(filters_used)
        is_large_result = record_count > LARGE_RESULT_THRESHOLD

        serializable_data = df.to_dict(orient="records")

        if is_large_result:
            print(f"Synthesizer Agent: Large result ({record_count} > {LARGE_RESULT_THRESHOLD}).")
            final_response = await generate_summary(self.llm, user_query, record_count, serializable_data)

            return {
                "final_response":  final_response,
                "response_html":   "",
                "record_count":    record_count,
                "data":            serializable_data,
                "insights":        [],
                "is_large_result": True,
            }
        
        print(f"Synthesizer Agent: Small result ({record_count} <= {LARGE_RESULT_THRESHOLD}).")

        llm_output = await self.build_small_result_response(
            state=state,
            data=serializable_data,
            record_count=record_count,
            display_filters=display_filters,
        )
        table_html = dataframe_to_compact_html(df, filters_used=display_filters)

        return {
            "final_response":  llm_output["final_response"],
            "response_html":   table_html,
            "record_count":    record_count,
            "data":            serializable_data, 
            "insights":        llm_output["insights"],
            "is_large_result": False,
        }