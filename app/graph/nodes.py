import asyncio,json
import pandas as pd
from app.core.state import AgentState
from app.utils.logger import logger
from app.core.exceptions import EntityAmbiguousError
from app.utils.helper_methods import extract_ast_filters
from app.utils.security import HarmoniaService

class HarmoniaGraphNodes:

    def __init__(self, query_rewriter, intent_agent, sql_agent, synthesizer, vector_tool, pandas_tool, db_client, guardrail_agent,redis_client):
        self.guardrail_agent = guardrail_agent
        self.query_rewriter = query_rewriter
        self.intent_agent = intent_agent
        self.sql_agent = sql_agent
        self.synthesizer = synthesizer
        self.vector_tool = vector_tool
        self.pandas_tool = pandas_tool
        self.db_client = db_client
        self.redis_client = redis_client

    # Handles the rewrite agent node
    async def rewrite_node(self, state: AgentState):
        messages = state.get("messages", [])
        base_query = state.get("current_query", "")
        current_sel = state.get("current_query", "").strip()

        # Reverse ambiguity scan
        if len(messages) > 0 and messages[-1].get("role") == "assistant" and "$$" in messages[-1].get("content", ""):
            options_presented = messages[-1].get("content", "").split("$$")
            
            # Verify if the input is actually a selection
            is_selection = False
            if ":" in current_sel:
                is_selection = True
            else:
                for opt in options_presented:
                    # Check if the user's input matches any part of the presented options
                    if current_sel.lower() in opt.lower() or current_sel.lower() == opt.lower():
                        is_selection = True
                        break
            
            # Process as Ambiguity Choice ONLY if it passed the check
            if is_selection:
                user_selected_dict = {}
                base_query = state.get("current_query", "")
                for i, m in reversed(list(enumerate(messages))):
                    if m.get("role") == "assistant" and "$$" in m.get("content", ""):
                        if i > 0 and messages[i-1].get("role") == "user":
                            base_query = messages[i-1].get("content", "")
                        break 
                
                if ":" in current_sel:
                    col, val = current_sel.split(":", 1)
                    user_selected_dict[col.strip().lower()] = val.strip()
                else:
                    user_selected_dict["clarified_value"] = current_sel

                return {
                    "turn_classification": "AMBIGUITY_CHOICE",
                    "rewritten_query": base_query,
                    "user_selected_value": user_selected_dict
                }
            else:
                logger.info("Rewrite Node: User ignored ambiguity options. Treating as a new query.")

        # Standard Classification (LLM handles STANDALONE vs FOLLOW_UP)
        classification = await self.query_rewriter(state)
        output_state = {
            "turn_classification": classification.query_type,
            "rewritten_query": classification.rewritten_query
        }
        
        # Purge past context on topic shifts
        if classification.query_type == "STANDALONE":
            output_state["semantic_state"] = {}

        return output_state

    # Intent Node 
    async def intent_node(self, state: AgentState):
        intent_result = await self.intent_agent(state)
        if state.get("turn_classification") == "FOLLOW_UP":
            past_filters = state.get("semantic_state", {}).get("active_filters", {})
            current_filters = intent_result.get("intents", {}) 
            
            merged_filters = {**past_filters, **current_filters}
            intent_result["intents"] = merged_filters
            logger.info(f"Intent Node: Merged FOLLOW_UP filters -> {merged_filters}")
            
        return intent_result

    # Save Cache data
    async def cache_save_node(self, state: AgentState):
        if state.get("final_response") and state.get("category") != "security_block":
            user_id = state.get("user_id")
            filters_to_save = state.get("filters_used") or state.get("validated_schema") or state.get("intents") or {}
            semantic_payload = {
                "active_filters": filters_to_save,
                "metric": state.get("metric"),
                "route": state.get("route")
            }
            await self.redis_client.save_semantic_state(user_id, semantic_payload)
            
        return {
            "user_selected_value": None,
            "is_ambiguous": False
        }

    # Initializes the Vector search and decides whether to do vector search or bypass vector search
    async def vector_node(self, state: AgentState):
        logger.info("Vector Node: Validating entities across all data slices...")
        user_query = state.get("rewritten_query") or state.get("current_query", "")
        active_filters = state.get("filters") or state.get("raw_filters", {})
        try:
            validated = await self.vector_tool.validate_filters(
                raw_filters=active_filters,
                route=state.get("route"),
                user_query=user_query
            )
            return {"validated_schema": validated}

        except EntityAmbiguousError as e:
            logger.warning(f"Ambiguity detected: {e.message}")
            options_text  = "$$".join([f"{opt}" for opt in e.options])
            clarification = f"{options_text}"
            user_id = state.get("user_id")
            if hasattr(self.redis_client, 'append_chat_message'):
                await self.redis_client.append_chat_message(user_id,"user", user_query)
                await self.redis_client.append_chat_message(user_id,"assistant", clarification)
            else:
                logger.error("Could not save ambiguity turn to Redis: Method not implemented.")
            return {
                "final_response": clarification,
                "route": "ambiguous",
                "response_type": "clarification",
                "clarification_options": e.options[:4],
                "intents": [],
                "is_ambiguous": True,
            }

    # Invokes the SQL Agent and generated the executable query.
    async def sql_node(self, state: AgentState):
        return await self.sql_agent(state)

    # Executes the SQL query generated by the SQL Agent
    async def execute_node(self, state: AgentState):
        logger.info("Execution Node: Running SQL queries...")
        queries = state.get("sql_queries", [])
        user_id = state.get("user_id", "")
        user_role = state.get("user_role", "employee")
        
        tasks = []
        for q in queries:
            raw_llm_sql = q.get("sql", "")
            try:
                secured_sql = await HarmoniaService.role_based_sql_filter(
                    user_role=user_role, 
                    user_id=user_id, 
                    sql_query=raw_llm_sql
                )
                logger.info(f"Secured SQL to execute:\n{secured_sql}")
                tasks.append(self.db_client.execute_query(secured_sql))
                
            except ValueError as e:
                logger.error(f"RBAC execution aborted: {e}")
                return {
                    "raw_db_results": [{"error": "Data access restricted. Security validation failed."}],
                    "query_result": [],
                    "final_response": "Data access restricted. Security validation failed."
                }
                
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_clean_results = []
        actual_filters = {}

        for idx, res in enumerate(results):
            query_def = queries[idx]
            query_name = query_def.get("name", f"Query_{idx+1}")
            
            # Handle Failed Queries (No more silent drops)
            if isinstance(res, Exception):
                logger.error(f"SQL Execution Error for '{query_name}': {res}")
                all_clean_results.append({
                    "query_name": query_name,
                    "error": f"Database execution failed: {str(res)}"
                })
                continue
                
            # Handle Successful Queries (No more breaks)
            successful_sql = query_def.get("sql", "")
            if successful_sql:
                actual_filters.update(extract_ast_filters(successful_sql))
                
            query_result = pd.DataFrame(res)
            
            if not query_result.empty:
                clean_records = json.loads(query_result.to_json(orient="records", date_format="iso"))
                all_clean_results.extend(clean_records)
        
        return {
            "raw_db_results": all_clean_results,
            "query_result": all_clean_results,
            "filters_used": actual_filters 
        }

    # Detects and Safeguards the user query against malicious keywords before sending it to next agent .
    async def guardrail_node(self, state: AgentState):
        messages = state.get("messages", [])
        if len(messages) > 0:
            last_msg = messages[-1]
            if last_msg.get("role") == "assistant" and "$$" in last_msg.get("content", ""):
                logger.info("Guardrail Node: Ambiguity clarification detected. Bypassing safety checks.")
                return {}

        user_query   = state.get("current_query", "")
        safety_check = await self.guardrail_agent(user_query)
        if not safety_check.is_safe:
            logger.warning(f"Guardrail triggered. LLM Reason: {safety_check.reason}")
            return {
                "category":       "security_block",
                "final_response": safety_check.reason, 
            }
        logger.info("Guardrail Node: Query passed safety checks.")
        return {}

    # Builds the final response for frontend to show the user
    async def synthesize_node(self, state: AgentState):
        response_type = state.get("response_type")

        if response_type in ("blocked", "clarification"):
            return {}

        category = state.get("category", "database_query")
        
        if category in ("greeting", "vague_or_off_topic"):
            logger.info(f"Synthesize Node: Fast-tracking conversational response for '{category}'")
            return {
                "final_response": state.get("direct_response") or "Hello! How can I help you with the data today?",
                "response_type": "text",
                "graph_payload": None
            }

        logger.info("Synthesize Node: Synthesizing database results...")
        result = await self.synthesizer(state)

        payload = result.get("graph_payload")
        result["response_type"] = "dashboard" if payload and payload.get("is_dashboard") else "text"
        return result

# Define Nodes Structure for RAS
class RASGraphNodes:
    def __init__(self,guardrail_agent,query_rewriter,intent_agent,sql_agent,synthesizer,vector_tool,db_client,redis_client):
        self.guardrail_agent  = guardrail_agent
        self.query_rewriter   = query_rewriter 
        self.intent_agent     = intent_agent
        self.sql_agent        = sql_agent
        self.synthesizer      = synthesizer
        self.vector_tool      = vector_tool
        self.db_client        = db_client
        self.redis_client     = redis_client

    # Detects and Safeguards the user query against malicious keywords before sending it to next agent .
    async def guardrail_node(self, state: AgentState):
        messages = state.get("messages", [])
        
        # Bypass guardrail if the user is just answering an ambiguity clarification
        if len(messages) > 0:
            last_msg = messages[-1]
            if last_msg.get("role") == "assistant" and "$$" in last_msg.get("content", ""):
                logger.info("Guardrail Node: Ambiguity clarification detected. Bypassing safety checks.")
                return {}

        user_query   = state.get("current_query", "")
        safety_check = await self.guardrail_agent(user_query)
        if not safety_check.is_safe:
            logger.warning(f"Guardrail triggered. LLM Reason: {safety_check.reason}")
            return {
                "category":       "security_block",
                "final_response": safety_check.reason, 
            }
        logger.info("Guardrail Node: Query passed safety checks.")
        return {}

    # Handles the rewrite agent node
    async def rewrite_node(self, state: AgentState):
        messages = state.get("messages", [])
        base_query = state.get("current_query", "")
        current_sel = state.get("current_query", "").strip()

        # Reverse ambiguity scan
        if len(messages) > 0 and messages[-1].get("role") == "assistant" and "$$" in messages[-1].get("content", ""):
            options_presented = messages[-1].get("content", "").split("$$")
            
            # Verify if the input is actually a selection
            is_selection = False
            if ":" in current_sel:
                is_selection = True
            else:
                for opt in options_presented:
                    if current_sel.lower() in opt.lower() or current_sel.lower() == opt.lower():
                        is_selection = True
                        break
            
            # Process as Ambiguity Choice ONLY if it passed the check
            if is_selection:
                user_selected_dict = {}
                base_query = state.get("current_query", "")
                base_query_index = 0
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        if i == 0 or (i > 0 and "$$" not in messages[i-1].get("content", "")):
                            base_query = messages[i].get("content", "")
                            base_query_index = i
                            break

                # 3. RECONSTRUCT PAST SELECTIONS: Scan forward to grab all previous choices in this chain
                for i in range(base_query_index,len(messages)):
                    if messages[i].get("role") == "assistant" and "$$" in messages[i].get("content", ""):
                        if i + 1 < len(messages) and messages[i+1].get("role") == "user":
                            past_sel = messages[i+1].get("content", "").strip()
                            if ":" in past_sel:
                                col, val = past_sel.split(":", 1)
                                user_selected_dict[col.strip().lower()] = val.strip()
                
                if ":" in current_sel:
                    col, val = current_sel.split(":", 1)
                    user_selected_dict[col.strip().lower()] = val.strip()
                else:
                    user_selected_dict["clarified_value"] = current_sel

                return {
                    "turn_classification": "AMBIGUITY_CHOICE",
                    "rewritten_query": base_query,
                    "user_selected_value": user_selected_dict
                }
            else:
                logger.info("Rewrite Node: User ignored ambiguity options. Treating as a new query.")

        # Standard Classification STANDALONE/FOLLOW_UP
        classification = await self.query_rewriter(state)
        output_state = {
            "turn_classification": classification.query_type,
            "rewritten_query": classification.rewritten_query
        }
        
        # Purge past context on topic shifts
        if classification.query_type == "STANDALONE":
            output_state["semantic_state"] = {}

        return output_state
        
    async def intent_node(self, state: AgentState):
        intent_result = await self.intent_agent(state)
        
        # Merge filters dynamically for FOLLOW_UP requests
        if state.get("turn_classification") == "FOLLOW_UP":
            past_filters = state.get("semantic_state", {}).get("active_filters", {})
            current_filters = intent_result.get("raw_filters", {}) 
            merged_filters = {**past_filters, **current_filters}
            
            intent_result["raw_filters"] = merged_filters
            
        return intent_result

    async def cache_save_node(self, state: AgentState):
        if state.get("final_response") and state.get("category") != "security_block":
            user_id = state.get("user_id")
            
            # Save Semantic State
            filters_to_save = state.get("raw_filters") or {}
            semantic_payload = {
                "active_filters": filters_to_save,
                "metric": state.get("metric"),
                "route": state.get("route")
            }
            await self.redis_client.save_semantic_state(user_id, semantic_payload)
            
            # Save Visual Data
            if not state.get("is_cached_hit"):
                await self.redis_client.save_response(
                    user_id=user_id,
                    language=state.get("language", "en"),
                    query=state.get("current_query"),
                    final_response=state["final_response"],
                    data=state.get("raw_db_results", [])
                )
                
        return {"user_selected_value": None, "is_ambiguous": False}

    # Resolves the column values from  Vector DB and Table Schema
    async def vector_node(self, state: AgentState):

        logger.info("Vector Node: Validating entities...")
        user_query = state.get("rewritten_query") or state.get("current_query", "")
        actual_user_selection = state.get("current_query", "") 
        active_filters = state.get("filters") or state.get("raw_filters", {})
        try:
            validated = await self.vector_tool.validate_filters(
                raw_filters=active_filters,
                route=state.get("route"),
                user_query=user_query
            )
            return {"validated_schema": validated}
        except EntityAmbiguousError as e:
            logger.warning(f"Ambiguity detected: {e.message}")
            options_text  = "$$".join([f"{opt}" for opt in e.options])
            clarification = f"{options_text}"
            user_id = state.get("user_id")
            
            if hasattr(self.redis_client, 'append_chat_message_ras'):
                await self.redis_client.append_chat_message_ras(user_id,"user", actual_user_selection)
                await self.redis_client.append_chat_message_ras(user_id,"assistant", clarification)
            else:
                logger.error("Could not save ambiguity turn to Redis: Method not implemented.")
            return {
                "final_response":        clarification,
                "route":                 "ambiguous",
                "validated_schema":      {},
                "clarification_options": e.options[:5],
                "is_ambiguous":          True,
            }

    # Builds the final SQL Query.
    async def sql_node(self, state: AgentState):
        return await self.sql_agent(state)
    
    async def execute_node(self, state: AgentState):
        logger.info("Execution Node: Running SQL queries...")
        queries = state.get("sql_queries", [])

        primary_raw_results = []
        successful_sql = None 
        tasks = [self.db_client.execute_query(q["sql"]) for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"SQL Execution Error: {res}")
                continue
            primary_raw_results = res
            successful_sql = queries[idx].get("sql", "") 
            break
                
        query_result = pd.DataFrame(primary_raw_results)
        
        actual_filters = {}
        if successful_sql:
            actual_filters = extract_ast_filters(successful_sql)
        
        return {
            "raw_db_results": primary_raw_results,
            "query_result":  query_result.to_dict(orient="records"),
            "filters_used": actual_filters 
        }
    
    # Generates the final response to display to the user.
    async def synthesize_node(self, state: AgentState):
        return await self.synthesizer(state)