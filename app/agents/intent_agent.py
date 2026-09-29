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
        user_selected = state.get("user_selected_value") or {}

        if state.get("turn_classification") == "AMBIGUITY_CHOICE":
            logger.info("Intent Agent: Bypassing LLM. Rebuilding filters from Turn 1 cache.")
            semantic_state = state.get("semantic_state") or {}
            filters_dict = semantic_state.get("active_filters", {})
            ambiguities_map = semantic_state.get("ambiguities_map", {})
            
            selected_values = []
            for sel_k, sel_vals in user_selected.items():
                if not isinstance(sel_vals, list):
                    sel_vals = [sel_vals]
                selected_values.extend([str(v).strip() for v in sel_vals])

            replacements = {}
            unmatched_selections = []
            
            for sel_val in selected_values:
                matched = False
                # COMPOSITE KEY MATCHING FIX
                for comp_key, options_list in ambiguities_map.items():
                    for opt in options_list:
                        if sel_val.lower() in str(opt).lower() or str(opt).lower() in sel_val.lower():
                            if str(comp_key) not in replacements:
                                replacements[str(comp_key)] = []
                            if sel_val not in replacements[str(comp_key)]:
                                replacements[str(comp_key)].append(sel_val)
                            
                            matched = True
                            break
                    if matched:
                        break
                if not matched:
                    unmatched_selections.append(sel_val)

            logger.info(f"Intent Agent: Mapped replacements -> {replacements}")

            for k, v in list(filters_dict.items()):
                new_filter_list = []
                v_list = v if isinstance(v, list) else [v]
                
                for item in v_list:
                    item_str = str(item)
                    # RECONSTRUCT THE COMPOSITE KEY HERE
                    comp_key = f"{k}::{item_str}"
                    
                    if comp_key in replacements:
                        for rep_val in replacements[comp_key]:
                            explicit_col = k
                            for sel_col, sel_vals in user_selected.items():
                                if not isinstance(sel_vals, list): sel_vals = [sel_vals]
                                if rep_val in sel_vals:
                                    if not sel_col.startswith("clarified_"):
                                        explicit_col = sel_col
                                    break
                            new_filter_list.append(f"__EXPLICIT__{explicit_col}__VAL__{rep_val}")
                    else:
                        new_filter_list.append(item)
                
                if len(new_filter_list) == 1 and not isinstance(v, list):
                    filters_dict[k] = new_filter_list[0]
                elif len(new_filter_list) > 0:
                    filters_dict[k] = new_filter_list
                else:
                    del filters_dict[k]
                        
            for unmatched in unmatched_selections:
                for sel_k, sel_vals in user_selected.items():
                    if not isinstance(sel_vals, list):
                        sel_vals = [sel_vals]
                    if unmatched in [str(v).strip() for v in sel_vals]:
                        if sel_k.startswith("clarified_"):
                            logger.info(f"Intent Agent: Discarding unmatched noise -> '{unmatched}'")
                            continue
                            
                        encoded_val = f"__EXPLICIT__{sel_k}__VAL__{unmatched}"
                        if sel_k not in filters_dict:
                            filters_dict[sel_k] = encoded_val
                        else:
                            if not isinstance(filters_dict[sel_k], list):
                                filters_dict[sel_k] = [filters_dict[sel_k]]
                            if encoded_val not in filters_dict[sel_k]:
                                filters_dict[sel_k].append(encoded_val)

            route = semantic_state.get("route", "sql")
            if route == "vague": 
                route = "sql"

            return {
                "route":           route,
                "metric":          semantic_state.get("metric"),
                "top_n":           None,
                "raw_filters":     filters_dict,
                "intent":          {"thought_process": "Resolved via cached ambiguity selections."},
                "validated_schema": {}, 
                "sql_queries":      []
            }
            
        # NORMAL FLOW STANDALONE / FOLLOW_UP Call the LLM
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
        
        user_prompt = state.get("rewritten_query") or state.get("current_query", "")
        
        try:
            response: IntentContract = await self.llm.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_class=IntentContract
            )
            
            logger.info(f"Intent Agent: Route determined -> {response.route}")
            print("\n" + "=" * 60)
            print("INTENT AGENT OUTPUT")
            print("=" * 60)
            pprint(response.model_dump())
            print("=" * 60)
            
            return {
                "route":           response.route,
                "metric":          response.metric,
                "top_n":           response.top_n,
                "raw_filters":     response.filters,
                "intent":          response.model_dump(),
                "validated_schema": {}, 
                "sql_queries":      []
            }

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