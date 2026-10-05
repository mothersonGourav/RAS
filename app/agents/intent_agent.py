from datetime import datetime
from pprint import pprint
from app.base.base_llm import BaseLLM
from app.models.pydantic_models import IntentContract
from app.prompts.prompt_registry import INTENT_SYSTEM_PROMPT
from app.utils.logger import logger
from app.utils.schema_loader import SchemaManager

class IntentAgent:
    def __init__(self, llm_client: BaseLLM, schema_manager: SchemaManager):
        self.llm            = llm_client
        self.schema_manager = schema_manager

    async def __call__(self, state: dict) -> dict:
        logger.info("Intent Agent: Analyzing query intent")
        
        # Schema
        try:
            semantic_schema = self.schema_manager.get_intent_context()
        except Exception as e:
            logger.error(f"Intent Agent Schema Fetch Error: {e}", exc_info=True)
            semantic_schema = "Schema not available."
            
        # Prompts
        system_prompt = (INTENT_SYSTEM_PROMPT
                            .replace("{current_time}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                            .replace("{semantic_schema}", semantic_schema)
                        )
        system_prompt = system_prompt.replace("{", "{{").replace("}", "}}")
        
        # Keep user prompt completely clean (the exact base query)
        user_prompt = state.get("rewritten_query") or state.get("current_query", "")
        
        try:
            
            user_selected = state.get("user_selected_value") or {}
           ##The Bypass Logic
            if state.get("turn_classification") == "AMBIGUITY_CHOICE" and user_selected:
                logger.info("Intent Agent: AMBIGUITY_CHOICE detected. Bypassing LLM generation.")
                pending = state.get("pending_semantic_state") or {}
                response = IntentContract(
                    thought_process="Bypassed LLM - Restored from pending state for ambiguity resolution.",
                    route=pending.get("route") or "sql",
                    target_table="tbl_ras_data",
                    metric=pending.get("metric"),
                    filters=pending.get("active_filters") or {},
                    top_n=pending.get("top_n")
                )
            else:
                # ORIGINAL LLM EXECUTION
                response: IntentContract = await self.llm.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_class=IntentContract
                )
            filters_dict = response.filters or {}

            # If user made an ambiguity choice, override/replace filters purely in Python
            if user_selected:
                logger.info(f"Intent Agent: Applying deterministic override with user selections: {list(user_selected.keys())}")
                
                for sel_k, sel_v in user_selected.items():
                    keys_to_remove = set()
                    
                    # 1. Clean the ambiguous string from OTHER columns to prevent duplicate filtering
                    for k, v in list(filters_dict.items()):
                        if k.lower() == sel_k.lower():
                            continue # Do NOT clean the target column we are currently injecting into
                            
                        if isinstance(v, list):
                            new_list = [item for item in v if str(item).lower() not in str(sel_v).lower()]
                            filters_dict[k] = new_list
                            if not new_list:
                                keys_to_remove.add(k)
                        else:
                            if isinstance(v, str) and str(v).lower() in str(sel_v).lower():
                                keys_to_remove.add(k)

                    for k in keys_to_remove:
                        logger.info(f"Intent Agent: Dropping ambiguous LLM filter '{k}'")
                        del filters_dict[k]
                    
                    # 2. Inject the selected value SAFELY (Promote to list if key already exists)
                    target_key = sel_k.lower()
                    
                    # Safely find if the target key exists case-insensitively
                    actual_key = next((k for k in filters_dict.keys() if k.lower() == target_key), sel_k)

                    if actual_key in filters_dict:
                        existing_val = filters_dict[actual_key]
                        if isinstance(existing_val, list):
                            if sel_v not in existing_val:
                                existing_val.append(sel_v)
                        else:
                            if str(existing_val).lower() != str(sel_v).lower():
                                # Promote existing scalar to a list to hold both constraints
                                filters_dict[actual_key] = [existing_val, sel_v]
                                logger.info(f"Intent Agent: Promoted '{actual_key}' to list to preserve multiple entities.")
                    else:
                        filters_dict[sel_k] = sel_v

                response.filters = filters_dict
                if response.route == "vague":
                    response.route = "sql"
                
            logger.info(f"Intent Agent: Route determined -> {response.route}")
            print("\n" + "=" * 60)
            print("INTENT AGENT OUTPUT")
            print("=" * 60)
            pprint(response.model_dump())
            print("=" * 60)
            
            # Prepare the output state payload
            output_state = {
                "route":           response.route,
                "metric":          response.metric,
                "top_n":           response.top_n,
                "raw_filters":     response.filters,
                "intent":          response.model_dump(),
                "interpreted_query": response.interpreted_query,
                "validated_schema": {}, 
                "sql_queries":      []
            }
            
            return output_state

        except Exception as e:
            logger.error(f"Intent Agent Error: {e}", exc_info=True)
            output_state_fail = {
                "route":           "vague",
                "metric":          None,
                "raw_filters":     {},
                "top_n":           None,
                "intent":          None,
                "final_response":  "I'm sorry, I couldn't process that request.",
            }
            if state.get("user_selected_value") or state.get("is_ambiguous"):
                output_state_fail["user_selected_value"] = None
                output_state_fail["is_ambiguous"] = False
                
            return output_state_fail