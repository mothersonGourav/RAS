from pprint import pprint
from datetime import datetime
from app.base.base_llm import BaseLLM
from app.utils.logger import logger
from app.utils.schema_loader import HarmoniaSchemaManager,RASSchemaManager
from app.utils.latency_tracker import measure_latency
from app.schemas.pydantic_models import IntentContract,IntentValidation
from app.prompts.prompt_registry import HARMONIA_INTENT_SYSTEM_PROMPT,RAS_INTENT_SYSTEM_PROMPT

class HarmoniaIntentAgent:
    """Analyzes user intent and produces a structured routing contract."""

    def __init__(self, llm_client: BaseLLM,schema_manager:HarmoniaSchemaManager):
        self.llm = llm_client
        self.schema_manager = schema_manager  

    @measure_latency
    async def __call__(self, state):
        logger.info("Intent Agent: Analyzing query intent")
        try:
            semantic_schema = self.schema_manager.get_intent_context()
        except Exception as e:
            logger.error(f"Intent Agent Schema Fetch Error: {str(e)}", exc_info=True)
            semantic_schema = "Schema not available."
            
        system_prompt = (HARMONIA_INTENT_SYSTEM_PROMPT
                                .replace("{current_time}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                .replace("{semantic_schema}", semantic_schema)
                            )
        system_prompt = system_prompt.replace("{", "{{").replace("}", "}}")
        user_prompt = state.get("rewritten_query") or state.get("current_query", "")
        if "(filtered for" in user_prompt:
            user_prompt = user_prompt.replace("{", "{{").replace("}", "}}")
        
        try:
            response: IntentValidation = await self.llm.generate_structured(
                system_prompt=system_prompt,
                user_prompt  =user_prompt,
                schema_class =IntentValidation
            )
            
            user_selected = state.get("user_selected_value") or {}
            print("User Selected ", user_selected)
            
            # START OF FIX
            if user_selected:
                logger.info(f"Intent Agent: Applying deterministic override with user selections: {list(user_selected.keys())}")
                filters_dict = response.intents or {}
                keys_to_remove = set()

                for sel_k, sel_v in user_selected.items():
                    for k, v in filters_dict.items():
                        # Remove if LLM guessed the same column name OR a substring of the correct value
                        if sel_k.lower() == k.lower() or (isinstance(v, str) and isinstance(sel_v, str) and v.lower() in sel_v.lower()):
                            keys_to_remove.add(k)

                for k in keys_to_remove:
                    logger.info(f"Intent Agent: Dropping vague LLM filter '{k}': '{filters_dict[k]}'")
                    del filters_dict[k]
                
                # Inject exact user-selected values directly into the LLM's response schema
                for k, v in user_selected.items():
                    filters_dict[k] = v

                response.intents = filters_dict
                
                # Force a valid route to break out of the ambiguity loop
                if response.route == "vague":
                    response.route = "database_query"
                    
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
                "raw_filters":     response.intents,
                "intents":         response.model_dump(),
                "validated_schema": {}, 
                "sql_queries":      []
            }
            
            # Clean up ambiguity state variables dynamically
            if state.get("user_selected_value") or state.get("is_ambiguous"):
                logger.info("Intent Agent: Cleaning ambiguity trackers from state.")
                output_state["user_selected_value"] = None
                output_state["is_ambiguous"] = False

            return output_state

        except Exception as e:
            logger.error(f"Intent Agent Error: {e}", exc_info=True)
            
            output_state_fail = {
                "route":           "vague",
                "metric":          None,
                "raw_filters":     {},
                "top_n":           None,
                "intents":         None,
                "final_response":  "I'm sorry, I couldn't process that request.",
            }
            if state.get("user_selected_value") or state.get("is_ambiguous"):
                output_state_fail["user_selected_value"] = None
                output_state_fail["is_ambiguous"] = False
                
            return output_state_fail

class RASIntentAgent:
    def __init__(self, llm_client: BaseLLM, schema_manager: RASSchemaManager):
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
        system_prompt = (RAS_INTENT_SYSTEM_PROMPT
                            .replace("{current_time}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                            .replace("{semantic_schema}", semantic_schema)
                        )
        system_prompt = system_prompt.replace("{", "{{").replace("}", "}}")
        
        # Keep user prompt completely clean (the exact base query)
        user_prompt = state.get("rewritten_query") or state.get("current_query", "")
        
        try:
            response: IntentContract = await self.llm.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_class=IntentContract
            )
            
            user_selected = state.get("user_selected_value") or {}
            filters_dict = response.filters or {}

            # If user made an ambiguity choice, override/replace filters purely in Python
            if user_selected:
                logger.info(f"Intent Agent: Applying deterministic override with user selections: {list(user_selected.keys())}")
                keys_to_remove = set()
                for sel_k, sel_v in user_selected.items():
                    for k, v in list(filters_dict.items()):
                        if isinstance(v, list):
                            new_list = [item for item in v if str(item).lower() not in str(sel_v).lower()]
                            
                            if sel_k.lower() == k.lower():
                                new_list.append(sel_v)
                                filters_dict[k] = new_list
                            else:
                                filters_dict[k] = new_list
                                if not new_list:
                                    keys_to_remove.add(k)
                                    
                        # Scalar Handling (Strings/Numbers)
                        else:
                            if sel_k.lower() == k.lower() or (isinstance(v, str) and str(v).lower() in str(sel_v).lower()):
                                keys_to_remove.add(k)

                for k in keys_to_remove:
                    if k in filters_dict:
                        logger.info(f"Intent Agent: Dropping ambiguous LLM filter '{k}'")
                        del filters_dict[k]
                
                # Inject exact user-selected values without any LLM alteration
                for k, v in user_selected.items():
                    if k not in filters_dict or not isinstance(filters_dict[k], list):
                        filters_dict[k] = v

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