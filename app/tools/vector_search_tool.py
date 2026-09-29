import re
import asyncio
from typing import Any, Tuple
from app.base.base_vector_db import BaseVectorDB
from app.base.base_rdbms import BaseRDBMS
from app.utils.config import Config
from app.utils.logger import logger
from app.utils.schema_loader import SchemaManager
from app.core.exceptions import EntityAmbiguousError,MultiEntityAmbiguousError
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

    def normalize_textual_math(self, val_str: str) -> str:
        """Converts LLM textual math hallucinations into strict operators."""
        val_lower = val_str.lower().strip()
        replacements = [
            (r"^(?:is\s+)?greater than or equal to\s*([\d.]+)", r">= \1"),
            (r"^(?:is\s+)?less than or equal to\s*([\d.]+)", r"<= \1"),
            (r"^(?:is\s+)?at least\s*([\d.]+)", r">= \1"),
            (r"^(?:is\s+)?more than\s*([\d.]+)", r"> \1"),
            (r"^(?:is\s+)?greater than\s*([\d.]+)", r"> \1"),
            (r"^(?:is\s+)?less than\s*([\d.]+)", r"< \1"),
            (r"^(?:is\s+)?under\s*([\d.]+)", r"< \1"),
            (r"^(?:is\s+)?over\s*([\d.]+)", r"> \1"),
            (r"^(?:is\s+)?equal to\s*([\d.]+)", r"= \1")
        ]
        for pattern, replacement in replacements:
            import re
            if re.match(pattern, val_lower):
                return re.sub(pattern, replacement, val_lower)
        return val_str
    
    # Process Single Filter values 
    async def process_single_filter(
        self, entity_label: str, raw_value: Any, route: str = None, user_query: str = ""
    ) -> Tuple[str, str]:
        
        if isinstance(raw_value, str) and raw_value.startswith("__EXPLICIT__"):
            parts = raw_value.split("__VAL__")
            explicit_col = parts[0].replace("__EXPLICIT__", "")
            actual_val = parts[1]
            logger.info(f"[VectorSearch] Intercepted explicit bypass: Col={explicit_col}, Val={actual_val}")
            sql_fragment = build_sql_fragment(explicit_col, explicit_col, actual_val, route)
            return entity_label, sql_fragment

        if isinstance(raw_value, dict):
            logger.info(f"[VectorSearch] Dictionary bounds detected for '{entity_label}', bypassing search.")
            dt_table, dt_col, _ = self.schema_manager.find_column_by_alias(entity_label)
            col_ref = dt_col or entity_label
            return entity_label, build_sql_fragment(col_ref, col_ref, raw_value, route)
        
        # 1. MATH NORMALIZATION FIX
        raw_value_str = str(raw_value).strip()
        raw_value_str = self.normalize_textual_math(raw_value_str) 

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

            _, expected_col, _ = self.schema_manager.find_column_by_alias(entity_label)
            expected_col = (expected_col or entity_label).lower()

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
                    # 2. COMPOSITE KEY FIX (Single Case)
                    comp_key = f"{entity_label}::{raw_value_str}"
                    raise EntityAmbiguousError(
                        f"{comp_key}",
                        options=distinct_options,
                    )

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
        validated = {}
        batch_ambiguities = {}

        for label, value in raw_filters.items():
            if isinstance(value, list):
                tasks = [self.process_single_filter(label, item, route, user_query) for item in value]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                fragments = []
                resolved_label = label 
                
                for idx, r in enumerate(results):
                    if isinstance(r, EntityAmbiguousError):
                        # 3. COMPOSITE KEY FIX (List Case)
                        comp_key = f"{label}::{value[idx]}"
                        batch_ambiguities[comp_key] = r.options
                    elif isinstance(r, Exception):
                        logger.error(f"[VectorSearch] Error processing '{value[idx]}': {r}")
                        raise r
                    else:
                        new_label, sql_fragment = r
                        resolved_label = new_label
                        fragments.append(sql_fragment)
                
                if fragments:
                    combined_fragment = f"({' OR '.join(fragments)})"
                    if resolved_label in validated:
                        clean_existing = validated[resolved_label].strip("()")
                        clean_new = combined_fragment.strip("()")
                        validated[resolved_label] = f"({clean_existing} OR {clean_new})"
                    else:
                        validated[resolved_label] = combined_fragment
                        
            else:
                try:
                    new_label, sql_fragment = await self.process_single_filter(label, value, route, user_query)
                    if new_label in validated:
                        clean_existing = validated[new_label].strip("()")
                        clean_new = sql_fragment.strip("()")
                        validated[new_label] = f"({clean_existing} OR {clean_new})"
                    else:
                        validated[new_label] = sql_fragment
                        
                except EntityAmbiguousError as e:
                    # 4. COMPOSITE KEY FIX (Scalar Case)
                    comp_key = f"{label}::{value}"
                    batch_ambiguities[comp_key] = e.options
                except Exception as e:
                    logger.error(f"[VectorSearch] Error processing '{value}': {e}")
                    raise e

        if batch_ambiguities:
            raise MultiEntityAmbiguousError(
                "Multiple entities require clarification.", 
                ambiguities=batch_ambiguities
            )

        return validated
