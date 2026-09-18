import re
import asyncio
from typing import Any, Tuple
from app.base.base_vector_db import BaseVectorDB
from app.base.base_rdbms import BaseRDBMS
from app.utils.config import Config
from app.utils.logger import logger
from app.utils.schema_loader import SchemaManager
from app.core.exceptions import EntityAmbiguousError
from app.utils.helper_methods import (
    build_sql_fragment, is_date_bypass,is_partial_match)

class VectorSearchTool:
    def __init__(
        self,
        vector_client: BaseVectorDB,
        rdbms_client: BaseRDBMS,
        schema_manager: SchemaManager
    ):
        self.vector_client   = vector_client
        self.rdbms           = rdbms_client
        self.schema_manager  = schema_manager
        self.AMBIGUITY_MARGIN = Config.AMBIGUITY_MARGIN
        self.bypass_pattern = re.compile(r"\b(combine)\b", re.IGNORECASE)

    # Checks if the condition is numerical
    def is_numerical_condition(self, value: str) -> bool:
        val_str = str(value).strip()
        return any(val_str.startswith(op) for op in [">=", "<=", ">", "<", "="]) or val_str.isdigit()

    # Builds the sql for the numerical value
    def parse_numeric_sql(self, column_name: str, raw_value: str) -> str:

        value_str = str(raw_value).strip()
        match = re.match(r"^(>=|<=|>|<|=)?\s*([\d.]+)", value_str)
        if match:
            operator = match.group(1) or "="
            number = match.group(2)
            return f"{column_name} {operator} {number}"            
        safe_val = value_str.replace("'", "''")
        return f"{column_name} = '{safe_val}'"
    
    # Extracts ALL valid exact or partial matches from a single Azure document
    def extract_valid_matches(self, raw_value_str: str, metadata: dict) -> list[dict]:

        raw_val_lower = raw_value_str.lower()
        matches = []
        
        for k, v in metadata.items():
            if v is not None:
                v_str = str(v)
                v_lower = v_str.lower()
                if v_lower == raw_val_lower or is_partial_match(raw_val_lower, v_lower):
                    matches.append({"col": k, "val": v_str})
                    
        return matches
    
    # Process Single Filter values 
    async def process_single_filter(
        self, entity_label: str, raw_value: Any, route: str = None, user_query: str = ""
    ) -> Tuple[str, str]:
        
        if isinstance(raw_value, dict):
            logger.info(f"[VectorSearch] Dictionary bounds detected for '{entity_label}', bypassing search.")
            dt_table, dt_col, _ = self.schema_manager.find_column_by_alias(entity_label)
            col_ref = dt_col or entity_label
            return entity_label, build_sql_fragment(col_ref, col_ref, raw_value, route)
        
        raw_value_str = str(raw_value).strip()
        dt_table, dt_col, _ = self.schema_manager.find_column_by_alias(entity_label)
        col_ref  = dt_col or entity_label 
        col_name = dt_col or entity_label
        should_bypass_ambiguity = bool(self.bypass_pattern.search(user_query))

        def fallback(override_col: str = None) -> str:
            final_col_ref = override_col or col_ref
            final_col_name = override_col or col_name
            return build_sql_fragment(final_col_ref, final_col_name, raw_value_str, route)

        try:
            if isinstance(raw_value, bool) or raw_value_str.lower() in ["true", "false"]:
                bool_val = "TRUE" if raw_value_str.lower() == "true" else "FALSE"
                logger.info(f"[VectorSearch] Boolean bypass triggered for column '{col_ref}' -> {bool_val}")
                return entity_label, f"{col_ref} = {bool_val}"
            
            if isinstance(raw_value, (int, float)) or self.is_numerical_condition(raw_value_str):
                return entity_label, self.parse_numeric_sql(col_ref, raw_value_str)

            if is_date_bypass(raw_value_str):
                return entity_label, build_sql_fragment(col_ref, col_name, raw_value_str, route)
            
            if entity_label.lower() == "approvers_list":
                logger.info(f"[VectorSearch] Exact column bypass triggered for '{entity_label}'.")
                return entity_label, build_sql_fragment(col_ref, col_name, raw_value_str, route)
            
            search_kwargs = {
                "query_text": raw_value_str,
                "top_k": 50,
                "target_table": "tbl_Ras_data" 
            }
            logger.info(f"[VectorSearch] Attempting Pure Value Search for: {raw_value_str!r}")
            results = await self.vector_client.search(**search_kwargs)
            # print("RESULTS FROM AZURE SEARCH :",results)
            
            if results:
                print(f"Top Score: {results[0].get('score', 0.0)}")
                print(f"Top Meta: {results[0].get('metadata')}")

            if not results:
                logger.warning(f"[VectorSearch] No results for {raw_value_str!r}, applying fallback.")
                return entity_label, fallback()

            ranked = sorted(
                [{"score": doc.get("score", 0.0), "metadata": doc.get("metadata", {})} for doc in results],
                key=lambda x: x["score"],
                reverse=True,
            )
            if not ranked:
                logger.warning(f"[VectorSearch] No valid ranked results found for {raw_value_str!r}. Applying fallback.")
                return entity_label, fallback()

            top_score = ranked[0]["score"]
            margin_matches = []
            seen_values = set()

            for hit in ranked:
                hit_score = hit["score"]
                
                if (top_score - hit_score) <= self.AMBIGUITY_MARGIN:
                    doc_matches = self.extract_valid_matches(raw_value_str, hit["metadata"])
                    for match in doc_matches:
                        v_lower = match["val"].lower()
                        col_name = match["col"].lower()
                        dedup_key = f"{col_name}::{v_lower}"
                        if dedup_key not in seen_values:
                            seen_values.add(dedup_key)
                            margin_matches.append(match)
                else:
                    break

            if not margin_matches:
                logger.info(f"[VectorSearch] No valid strings found. Falling back to SQL LIKE.")
                return entity_label, fallback()
            
            for idx, m in enumerate(margin_matches, 1):
                print(f"[{idx}] Column: '{m['col']}' | Value: '{m['val']}'")

            # Resolve expected column
            _, expected_col, _ = self.schema_manager.find_column_by_alias(entity_label)
            expected_col = (expected_col or entity_label).lower()

            # Ambiguity Multiple valid distinct strings found
            if len(margin_matches) > 1:
                
                intent_matches = [
                    m for m in margin_matches 
                    if m["col"].lower() == expected_col and m["val"].lower() == raw_value_str.lower()
                ]
                if len(intent_matches) == 1:
                    match = intent_matches[0]
                    best_field = match["col"]
                    stored_value = match["val"]
                    
                    logger.info(f"[VectorSearch] Ambiguity resolved via Intent Bias. Selected '{best_field}' -> '{stored_value}'")
                    sql_fragment = build_sql_fragment(best_field, best_field, stored_value, route)
                    return best_field, sql_fragment
                
                distinct_options = [f"{m['col'].title()}:{m['val']}" for m in margin_matches[:4]]
                
                if should_bypass_ambiguity:
                    logger.info(f"[VectorSearch] Ambiguity bypassed for {raw_value_str!r}. Generating OR block.")
                    or_conditions = []
                    for match in margin_matches[:5]:
                        f_col_ref = match["col"] 
                        safe_val = str(match["val"]).replace("'", "''")
                        or_conditions.append(f"LOWER({f_col_ref}) LIKE '%{safe_val.lower()}%'")
                    
                    sql_fragment = f"({' OR '.join(or_conditions)})"
                    return entity_label, sql_fragment
                else:
                    raise EntityAmbiguousError(
                        f"{raw_value_str}",
                        options=distinct_options,
                    )

            # Single Match
            elif len(margin_matches) == 1:
                match = margin_matches[0]
                best_field = match["col"]
                stored_value = match["val"]
                logger.info(f"[VectorSearch] Single match mapped to column '{best_field}' -> '{stored_value}'")
                sql_fragment = build_sql_fragment(best_field, best_field, stored_value, route)
                return best_field, sql_fragment

        except EntityAmbiguousError:
            raise
        except Exception as e:
            logger.error(f"[VectorSearch] Unexpected error: {e}", exc_info=True)
            return entity_label, fallback()

    async def validate_filters(self, raw_filters: dict, route: str = None, user_query: str = "") -> dict:
        if not raw_filters:
            return {}

        logger.info(f"[VectorSearch] Validating {len(raw_filters)} filter(s), route={route!r}")

        async def process_key(label: str, value: Any):
            if isinstance(value, list):
                tasks     = [self.process_single_filter(label, item, route, user_query) for item in value]
                results   = await asyncio.gather(*tasks)
                fragments = [r[1] for r in results]
                return label, f"({' OR '.join(fragments)})"
            return await self.process_single_filter(label, value, route, user_query)

        tasks   = [process_key(label, value) for label, value in raw_filters.items()]
        results = await asyncio.gather(*tasks)

        validated = {}
        for new_label, sql_fragment in results:
            validated[new_label] = sql_fragment

        return validated



