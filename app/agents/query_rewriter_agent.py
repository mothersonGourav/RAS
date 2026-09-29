import json
from datetime import datetime
from app.base.base_llm import BaseLLM
from app.prompts.prompt_registry import QUERY_REWRITER_PROMPT
from app.utils.logger import logger
from app.models.pydantic_models import QueryClassificationContract

class QueryRewriterAgent:
    def __init__(self, llm_client: BaseLLM): 
        self.llm = llm_client

    async def __call__(self, state: dict) -> QueryClassificationContract:
        logger.info("Query Rewriter: Reconciling raw text against chat history...")
        
        current_query = state.get("current_query", "")
        semantic_state = state.get("semantic_state") or {}
        messages = state.get("messages", [])

        # Build the full formatted history
        history_text = "\n".join([
            f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}" 
            for msg in messages
        ]) if messages else "No previous history."
        
        state_text = json.dumps(semantic_state, default=str) if semantic_state else "No previous state."

        # Ground the prompt with Time and User Identity
        system_prompt_grounded = (QUERY_REWRITER_PROMPT
            .replace("{current_time}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )

        user_prompt = (
            f"<previous_semantic_state>\n{state_text}\n</previous_semantic_state>\n\n"
            f"<full_chat_history>\n{history_text}\n</full_chat_history>\n\n"
            f"<latest_query>\n{current_query}\n</latest_query>\n"
        )

        try:
            response: QueryClassificationContract = await self.llm.generate_structured(
                system_prompt=system_prompt_grounded,
                user_prompt=user_prompt,
                schema_class=QueryClassificationContract
            )
            
            logger.info(f"Query Rewriter: {response.query_type} -> '{response.rewritten_query}'")
            return response

        except Exception as e:
            logger.error(f"Query Rewriter Error: {e}. Falling back to STANDALONE.", exc_info=True)
            return QueryClassificationContract(query_type="STANDALONE", rewritten_query=current_query)