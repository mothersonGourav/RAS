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
                
                # Remove ambiguous/redundant filters matching user selections
                keys_to_remove = set()
                for sel_k, sel_v in user_selected.items():
                    for k, v in filters_dict.items():
                        # If the LLM guessed a conflicting column or a substring of the selected value, remove it
                        if str(v).lower() in str(sel_v).lower() or sel_k.lower() == k.lower():
                            keys_to_remove.add(k)

                for k in keys_to_remove:
                    logger.info(f"Intent Agent: Dropping ambiguous LLM filter '{k}': '{filters_dict[k]}'")
                    del filters_dict[k]
                
                # Inject exact user-selected values without any LLM alteration
                for k, v in user_selected.items():
                    filters_dict[k] = v

                response.filters = filters_dict
                # Ensure the route is set to SQL if a concrete entity was selected
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
