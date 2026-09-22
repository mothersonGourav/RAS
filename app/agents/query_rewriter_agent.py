import json
from app.base.base_llm import BaseLLM
from app.utils.logger import logger
from datetime import datetime
from app.prompts.prompt_registry import HARMONIA_QUERY_REWRITER_PROMPT,RAS_QUERY_REWRITER_PROMPT
from app.schemas.pydantic_models import QueryClassificationContract

class HarmoniaQueryRewriterAgent:
    def __init__(self, llm_client: BaseLLM):
        self.llm = llm_client

    async def __call__(self, state: dict) -> QueryClassificationContract:
        logger.info("Query Rewriter: Resolving coreferences across full chat history")
        
        current_query = state.get("current_query", "")
        semantic_state = state.get("semantic_state") or {}
        messages = state.get("messages", [])
        user_id = state.get("user_id", "Unknown")
        user_role = state.get("user_role", "employee")
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history_text = "\n".join([
            f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}" 
            for msg in messages
        ]) if messages else "No previous history."
        
        state_text = json.dumps(semantic_state, default=str) if semantic_state else "No previous state."
        system_prompt = f"""You are a Conversational Context Engine.
Your job is to read the Full Chat History and rewrite the User's Latest Query into a clean, standalone database request.
CURRENT TIME: {current_time}

<user_identity>
The user currently speaking has the GlobalUserId: {user_id}
Their role is: {user_role}
CRITICAL: If the user says "I", "me", "my", or "my team", you MUST replace it with their GlobalUserId ({user_id}) in the rewritten query.
</user_identity>

<rules>
1. TIME REFERENCES: Use the Current Time provided above to resolve relative time references like "this year", "last month", or "today" into absolute dates/years.
2. RESOLVE PRONOUNS: If the user says "what about his leaves", scan the Full Chat History to find who "he" is, and use their actual name.
3. STANDALONE CLASSIFICATION: If the user changes topics entirely (or reverts to a topic from much earlier in the chat that does not match the 'Previous Semantic State'), classify as STANDALONE and write a fully self-contained query.
4. FOLLOW_UP CLASSIFICATION: If the user is applying a minor filter or drill-down to the very last thing discussed in the 'Previous Semantic State', classify as FOLLOW_UP.
</rules>
"""
        user_prompt = (
            f"<previous_semantic_state>\n{state_text}\n</previous_semantic_state>\n\n"
            f"<full_chat_history>\n{history_text}\n</full_chat_history>\n\n"
            f"<latest_query>\n{current_query}\n</latest_query>\n"
        )

        try:
            response: QueryClassificationContract = await self.llm.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_class=QueryClassificationContract
            )
            logger.info(f"Query Rewriter: {response.query_type} -> '{response.rewritten_query}'")
            return response

        except Exception as e:
            logger.error(f"Query Rewriter Error: {e}", exc_info=True)
            return QueryClassificationContract(query_type="STANDALONE", rewritten_query=current_query)


class RASQueryRewriterAgent:
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
        system_prompt_grounded = (RAS_QUERY_REWRITER_PROMPT
            .replace("{current_time}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            # .replace("{user_id}", user_id)
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