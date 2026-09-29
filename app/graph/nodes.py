import asyncio,re
import pandas as pd
from app.core.state import AgentState
from app.core.exceptions import EntityAmbiguousError,MultiEntityAmbiguousError
from app.utils.logger import logger
from app.utils.helper_methods import extract_ast_filters

# Define Nodes Structure
class GraphNodes:
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

    async def rewrite_node(self, state: AgentState):
        messages = state.get("messages", [])
        base_query = state.get("current_query", "")
        current_sel = state.get("current_query", "").strip()

        if len(messages) > 0 and messages[-1].get("role") == "assistant" and "$$" in messages[-1].get("content", ""):
            import re
            
            prev_content = messages[-1].get("content", "")
            valid_options = prev_content.split("$$")[1:] if "$$" in prev_content else []
            valid_options.sort(key=len, reverse=True)
            
            found_selections = []
            remaining_text = current_sel
            
            for opt in valid_options:
                opt_clean = opt.strip()
                opt_val = opt_clean.split(":", 1)[-1].strip() if ":" in opt_clean else opt_clean
                
                if opt_clean.lower() in remaining_text.lower():
                    found_selections.append(opt_clean)
                    remaining_text = re.compile(re.escape(opt_clean), re.IGNORECASE).sub(" ", remaining_text)
                elif opt_val.lower() in remaining_text.lower() and len(opt_val) > 2:
                    found_selections.append(opt_clean)
                    remaining_text = re.compile(re.escape(opt_val), re.IGNORECASE).sub(" ", remaining_text)
                    
            raw_selections = [s.strip() for s in re.split(r',|\n', remaining_text) if s.strip()]
            all_selections = found_selections + raw_selections
            
            is_valid_turn = False
            user_selected_dict = {}

            def add_selection(key, value):
                k = key.strip().lower()
                v = value.strip()
                if k not in user_selected_dict:
                    user_selected_dict[k] = []
                if v not in user_selected_dict[k]:
                    user_selected_dict[k].append(v)

            for idx, sel in enumerate(all_selections):
                if ":" in sel:
                    is_valid_turn = True
                    col, val = sel.split(":", 1)
                    add_selection(col, val)
                else:
                    add_selection(f"clarified_value_{idx}", sel)

            if not is_valid_turn and len(all_selections) > 0:
                prev_msg = messages[-1].get("content", "").lower()
                if any(sel.lower() in prev_msg for sel in all_selections):
                    is_valid_turn = True

            if is_valid_turn:
                base_query_index = 0
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        if i == 0 or (i > 0 and "$$" not in messages[i-1].get("content", "")):
                            base_query = messages[i].get("content", "")
                            base_query_index = i
                            break

                for i in range(base_query_index, len(messages)):
                    if messages[i].get("role") == "assistant" and "$$" in messages[i].get("content", ""):
                        if i + 1 < len(messages) and messages[i+1].get("role") == "user":
                            past_sel_text = messages[i+1].get("content", "")
                            
                            p_valid_options = messages[i].get("content", "").split("$$")[1:]
                            p_valid_options.sort(key=len, reverse=True)
                            
                            p_found = []
                            p_remaining = past_sel_text
                            
                            for opt in p_valid_options:
                                opt_clean = opt.strip()
                                opt_val = opt_clean.split(":", 1)[-1].strip() if ":" in opt_clean else opt_clean
                                
                                if opt_clean.lower() in p_remaining.lower():
                                    p_found.append(opt_clean)
                                    p_remaining = re.compile(re.escape(opt_clean), re.IGNORECASE).sub(" ", p_remaining)
                                elif opt_val.lower() in p_remaining.lower() and len(opt_val) > 2:
                                    p_found.append(opt_clean)
                                    p_remaining = re.compile(re.escape(opt_val), re.IGNORECASE).sub(" ", p_remaining)
                                    
                            # AMPERSAND FIX applied here too
                            p_raw = [s.strip() for s in re.split(r',|\n', p_remaining) if s.strip()]
                            p_all = p_found + p_raw
                            
                            for p_idx, ps in enumerate(p_all):
                                if ":" in ps:
                                    col, val = ps.split(":", 1)
                                    add_selection(col, val)
                                else:
                                    add_selection(f"clarified_past_{i}_{p_idx}", ps)

                return {
                    "turn_classification": "AMBIGUITY_CHOICE",
                    "rewritten_query": base_query,
                    "user_selected_value": user_selected_dict
                }
            else:
                logger.info("Rewrite Node: User ignored ambiguity options. Treating as a new query.")

        classification = await self.query_rewriter(state)
        output_state = {
            "turn_classification": classification.query_type,
            "rewritten_query": classification.rewritten_query
        }
        if classification.query_type == "STANDALONE":
            output_state["semantic_state"] = {}

        return output_state

    async def intent_node(self, state: AgentState):
        intent_result = await self.intent_agent(state)
        
        if state.get("turn_classification") == "FOLLOW_UP":
            past_filters = state.get("semantic_state", {}).get("active_filters", {})
            current_filters = intent_result.get("raw_filters", {}) 
            
            # Purge old domains when overridden
            geo_keys = {"site", "region", "site_country", "supplier_country"}
            if any(k in current_filters for k in geo_keys):
                past_filters = {k: v for k, v in past_filters.items() if k not in geo_keys}
                
            time_keys = {"fiscalyear", "year", "originated_on", "final_approved_on"}
            if any(k in current_filters for k in time_keys):
                past_filters = {k: v for k, v in past_filters.items() if k not in time_keys}

            merged_filters = {**past_filters, **current_filters}
            merged_filters = {k: v for k, v in merged_filters.items() if v is not None}
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
            
        except MultiEntityAmbiguousError as e:
            logger.warning(f"Multiple Ambiguities detected: {list(e.ambiguities.keys())}")
            all_options = []
            clarification_lines = ["I found multiple matches. Please select one for each:"]
            
            for comp_key, options in e.ambiguities.items():
                col_name, raw_val = comp_key.split("::", 1)
                clarification_lines.append(f"\nFor '{raw_val}' in {col_name.replace('_', ' ').title()}:")
                clarification_lines.append(" | ".join(options[:5]))
                all_options.extend(options[:5])
                
            clarification_text = "\n".join(clarification_lines) + "\n$$" + "$$".join(all_options)
            user_id = state.get("user_id")
            
            semantic_payload = {
                "active_filters": active_filters,
                "metric": state.get("metric"),
                "route": state.get("route"),
                "ambiguities_map": e.ambiguities 
            }
            if hasattr(self.redis_client, 'save_semantic_state'):
                await self.redis_client.save_semantic_state(user_id, semantic_payload)

            if hasattr(self.redis_client, 'append_chat_message'):
                await self.redis_client.append_chat_message(user_id, "user", actual_user_selection)
                await self.redis_client.append_chat_message(user_id, "assistant", clarification_text)
                
            return {
                "final_response":        clarification_text,
                "route":                 "ambiguous",
                "validated_schema":      {},
                "clarification_options": all_options, 
                "is_ambiguous":          True,
            }
            
        except EntityAmbiguousError as e:

            logger.warning(f"Ambiguity detected: {e.message}")
            options_text  = "$$".join([f"{opt}" for opt in e.options])
            
            # Unpack composite key here as well
            col_name, raw_val = e.message.split("::", 1)
            clarification = f"For '{raw_val}' in {col_name.replace('_', ' ').title()}:\n{options_text}"
            
            user_id = state.get("user_id")
            
            semantic_payload = {
                "active_filters": active_filters,
                "metric": state.get("metric"),
                "route": state.get("route"),
                "ambiguities_map": {e.message: e.options}
            }
            if hasattr(self.redis_client, 'save_semantic_state'):
                await self.redis_client.save_semantic_state(user_id, semantic_payload)
            
            if hasattr(self.redis_client, 'append_chat_message'):
                await self.redis_client.append_chat_message(user_id,"user", actual_user_selection)
                await self.redis_client.append_chat_message(user_id,"assistant", clarification)
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