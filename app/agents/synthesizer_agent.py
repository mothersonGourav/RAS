import re
import pandas as pd
from app.utils.logger import logger
from app.utils.html_util import dataframe_to_compact_html
from app.utils.helper_methods import clean_filters_for_display, generate_summary
from app.prompts.prompt_registry import SYNTHESIZER_SYSTEM_PROMPT
from app.utils.config import Config
from app.models.pydantic_models import SynthesizerContract

# Load threshold value from config
LARGE_RESULT_THRESHOLD = Config.LARGE_RESULT_THRESHOLD
class SynthesizerAgent:
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
            parsed_result: SynthesizerContract = await self.llm.generate_structured(
                system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
                user_prompt=(
                    f"User asked: {user_query}\n"
                    f"RECORD COUNT: {record_count}\n"
                    f"METRIC: {metric or 'N/A'}\n"
                    f"FILTERS: {filter_text}\n\n"
                    f"DATA:\n{data_text}\n\n"
                    f"Generate 'response', 'insights', and 'follow_up_questions'."
                ),
                schema_class=SynthesizerContract
            )
            summary = parsed_result.response.strip()
            
            if not summary:
                summary = f"Successfully retrieved {record_count} record{'s' if record_count != 1 else ''}."

            return {
                "final_response": summary, 
                "insights": parsed_result.insights, 
                "follow_up_questions": parsed_result.follow_up_questions
            }

        except Exception as e:           
            logger.error(f"Small result LLM error: {e}", exc_info=True)
            return {
                "final_response": f"Successfully retrieved {record_count} record{'s' if record_count != 1 else ''}.",
                "insights":       [],
                "follow_up_questions": []
            }
        except Exception as e:
            logger.error(f"Small result LLM error: {e}", exc_info=True)
            return {
                "final_response": f"Successfully retrieved {record_count} record{'s' if record_count != 1 else ''}.",
                "insights":       [],
                "follow_up_questions": []
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
                "follow_up_questions": [],
                "is_large_result": False,
            }

        record_count    = len(df)
        user_query      = state.get("current_query", "Results")
        display_filters = clean_filters_for_display(filters_used)
        is_large_result = record_count > LARGE_RESULT_THRESHOLD

        serializable_data = df.to_dict(orient="records")

        if is_large_result:
            print(f"Synthesizer Agent: Large result ({record_count} > {LARGE_RESULT_THRESHOLD}).")
            large_result_data = await generate_summary(self.llm, user_query, record_count, serializable_data)

            return {
                "final_response":  large_result_data.get("final_response", "Result summarized."),
                "response_html":   "",
                "record_count":    record_count,
                "data":            serializable_data,
                "insights":        [],
                "follow_up_questions": large_result_data.get("follow_up_questions", []),
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
            "follow_up_questions": llm_output.get("follow_up_questions", []),
            "is_large_result": False,
        }