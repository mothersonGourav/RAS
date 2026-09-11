import re
from app.base.base_llm import BaseLLM
from app.base.base_rdbms import BaseRDBMS
from app.models.pydantic_models import SQLContract
from app.prompts.prompt_registry import SQL_SYSTEM_PROMPT
from app.utils.logger import logger
from app.clients.vector.chroma_client import ChromaSearchClient
from app.utils.config import Config
from app.utils.helper_methods import resolve_fiscal_year_filter
import tiktoken
from app.utils.schema_loader import SchemaManager
from app.utils.config import Config

FEW_SHOT_THRESHOLD = Config.FEW_SHOT_THRESHOLD 

class SQLAgent:
    def __init__(self, llm_client: BaseLLM, db_client: BaseRDBMS, schema_manager: SchemaManager):
        self.llm            = llm_client
        self.db             = db_client
        self.schema_manager = schema_manager
        try:
            self.tokenizer = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        try:
            self.few_shot_chroma = ChromaSearchClient(
                db_dir          = Config.CHROMA_DB_PATH,
                collection_name = Config.CHROMA_COLLECTION,
                api_key         = Config.OPENAI_API_KEY,
            )
            logger.info("[FewShot] ChromaDB few-shot client initialized successfully.")
        except Exception as e:
            logger.error(f"[FewShot] ChromaDB init failed: {e}", exc_info=True)
            self.few_shot_chroma = None

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    @staticmethod
    def extract_first_sql(raw: str) -> str:
        match = re.search(
            r'\b(SELECT|WITH|INSERT|UPDATE|DELETE)\b[\s\S]+?;',
            raw,
            re.IGNORECASE,
        )
        if match:
            return match.group(0).strip()
        return raw.strip()

    @staticmethod
    def clean_sql(sql: str) -> str:
        """Apply standard SQL cleanup replacements."""
        return (
            sql
            .replace("WITH (NOLOCK)", "")
            .replace("WITH(NOLOCK)", "")
            .replace("[", "")
            .replace("]", "")
            .replace("SELECT TOP", "SELECT")
            .replace("ISNULL(", "COALESCE(")
            .replace("GETDATE()", "NOW()")
        )

    @staticmethod
    def strip_fiscal_filters(sql: str) -> str:
       
        sql = re.sub(
            r"\b(AND|OR)?\s*fiscalyear\s+IN\s*\([^)]+\)",
            "", sql, flags=re.IGNORECASE
        )
        sql = re.sub(
            r"\b(AND|OR)?\s*fiscalyear\s*=\s*'[^']+'",
            "", sql, flags=re.IGNORECASE
        )
        sql = re.sub(
            r"\b(AND|OR)?\s*fiscalyear\s+IN\s*\(\s*SELECT\s+DISTINCT\s+fiscalyear\b[^)]*\)",
            "", sql, flags=re.IGNORECASE
        )
        sql = re.sub(r"WHERE\s+(AND|OR)\b", "WHERE", sql, flags=re.IGNORECASE)
        sql = re.sub(r"WHERE\s*$", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\s{2,}", " ", sql)
        return sql.strip()

    async def __call__(self, state):
        logger.info("SQL Agent: Drafting SQL queries...")

        # Schema 
        try:
            semantic_schema = self.schema_manager.get_sql_context()
            semantic_schema = semantic_schema.replace("{", "{{").replace("}", "}}")
        except Exception as e:
            logger.error(f"SQL Agent Schema Error: {str(e)}", exc_info=True)
            semantic_schema = "Schema unavailable."

        user_query         = (state.get("rewritten_query")).lower()
        relevant_examples  = []
        formatted_examples = []
        few_shot_examples  = ""

        # Few-shot retrieval
        if self.few_shot_chroma:
            print(f"[FewShot] Searching ChromaDB with query: '{user_query}'")
            raw_examples = await self.few_shot_chroma.search_few_shots(
                query_text=user_query,
                top_k=3,
            )
            print("RAW CHROMA SCORES:")
            for ex in raw_examples:
                print(f"  score={ex['similarity']:.4f} | {ex['question'][:80]}")

            # Threshold: 0.70 for retrieval
            relevant_examples = [ex for ex in raw_examples if ex.get("similarity", 0) > Config.SIMILARITY_SCORE]
            if not relevant_examples:
                logger.info("[FewShot] No examples above similarity threshold 0.70 — skipping.")
            else:
                logger.info(f"[FewShot] Using {len(relevant_examples)} example(s) above 0.80 threshold.")

        # Format examples (extract first SQL to avoid malformed entries)
        for i, item in enumerate(relevant_examples, start=1):
            clean = self.extract_first_sql(item["sql"])
            clean = self.strip_fiscal_filters(clean) 
            sql   = clean.replace("{", "{{").replace("}", "}}")
            formatted_examples.append(
                f"\nEXAMPLE {i}\n\nQUESTION:\n{item['question']}\n\nSQL (structure only — fiscal year filters removed, use FILTERS section):\n{sql}\n"
            )

        few_shot_examples = "\n".join(formatted_examples)

        # Filters
        validated_schema = state.get("validated_schema") or {}
        raw_filters      = state.get("raw_filters") or {}
        active_filters   = validated_schema if validated_schema else raw_filters

        comparison_cols = [
            k for k, v in active_filters.items()
            if isinstance(v, list) and len(v) > 1
        ]

        if validated_schema:
            filter_parts = []
            for k, v in validated_schema.items():
                raw_val     = raw_filters.get(k)
                dynamic_sql = resolve_fiscal_year_filter(raw_val)
                if dynamic_sql:
                    filter_parts.append(f"- {dynamic_sql}")
                    logger.info(f"[SQLAgent] Dynamic fiscal year filter: {dynamic_sql}")
                else:
                    filter_parts.append(f"- {v}")

            filters_str        = "\n".join(filter_parts)
            prompt_instruction = (
                "CRITICAL: Use these PRE-RESOLVED SQL conditions VERBATIM in the WHERE clause.\n"
                "DO NOT re-derive or guess filter values from the query text.\n"
                "The values below are already resolved and must be copied exactly as-is:"
            )

        elif raw_filters:
            filter_parts = []
            for k, v in raw_filters.items():
                dynamic_sql = resolve_fiscal_year_filter(v)
                if dynamic_sql:
                    filter_parts.append(f"- {dynamic_sql}")
                    logger.info(f"[SQLAgent] Dynamic fiscal year filter: {dynamic_sql}")
                else:
                    filter_parts.append(f"- {k}: {v}")

            filters_str        = "\n".join(filter_parts)
            prompt_instruction = "Use EXTRACTED FILTERS exactly as provided in WHERE clause."

        else:
            filters_str        = "None"
            prompt_instruction = " "

        comparison_instruction = ""
        if comparison_cols:
            comparison_instruction = (
                f"\n\nCRITICAL COMPARISON RULE:\n"
                f"User compares values for: {comparison_cols}\n"
                f"You MUST include these columns in SELECT and GROUP BY."
            )

        few_shot_enforcement = ""
        if relevant_examples:
            best           = relevant_examples[0]
            best_score     = best.get("similarity", 0)
            best_sql_clean = self.extract_first_sql(best["sql"])
            best_sql_esc   = best_sql_clean.replace("{", "{{").replace("}", "}}")

            if best_score >= FEW_SHOT_THRESHOLD:
                logger.info(f"[FewShot] High similarity match (score={best_score:.4f}) — enforcing structural reference.")
                best_sql_stripped = self.strip_fiscal_filters(best_sql_clean)  
                best_sql_esc     = best_sql_stripped.replace("{", "{{").replace("}", "}}")
                print(f"\n[FewShot] STRUCTURAL REFERENCE MODE (similarity={best_score:.4f}) — LLM will reuse structure but apply updated filters.")
                print(f"MATCHED QUESTION : {best['question']}")
                print(f"REFERENCE SQL (fiscal filters stripped):\n{best_sql_stripped}")

                few_shot_enforcement = (
                    f"\n\n{'='*60}\n"
                    f"HIGH SIMILARITY FEW-SHOT MATCH (score={best_score:.4f})\n"
                    f"{'='*60}\n"
                    f"A closely matching question was found in the example database.\n\n"
                    f"USE THE SQL BELOW AS A STRUCTURAL REFERENCE ONLY:\n"
                    f"  - Replicate the SELECT columns, table JOINs, aggregation logic, and GROUP BY.\n"
                    f"  - The WHERE clause has been INTENTIONALLY STRIPPED of fiscal year filters.\n"
                    f"  - You MUST insert the fiscal year filter from the FILTERS section above.\n"
                    f"  - The WHERE clause MUST be built EXCLUSIVELY from the FILTERS section above.\n"
                    f"  - The resolved filter values in the FILTERS section are already correct — use them verbatim.\n\n"
                    f"REFERENCE QUESTION:\n{best['question']}\n\n"
                    f"REFERENCE SQL (structure only — fiscal year filters removed):\n{best_sql_esc}\n"
                    f"\nREMINDER: Build the WHERE clause using ONLY the FILTERS section above. "
                    f"DO NOT invent or hardcode any fiscal year values.\n"
                    f"{'='*60}\n"
                )
            else:
                logger.info(f"[FewShot] Moderate similarity (score={best_score:.4f}) — using as soft hint only.")
                best_sql_stripped = self.strip_fiscal_filters(best_sql_clean)  
                best_sql_esc     = best_sql_stripped.replace("{", "{{").replace("}", "}}")
                few_shot_enforcement = (
                    f"\n\n{'='*60}\n"
                    f"FEW-SHOT HINT (score={best_score:.4f})\n"
                    f"{'='*60}\n"
                    f"A loosely related question was found. Use it as a general hint for structure only.\n"
                    f"Build the WHERE clause ONLY from the FILTERS section above.\n"
                    f"DO NOT copy any fiscal year or date filters from this hint SQL.\n\n"
                    f"HINT QUESTION:\n{best['question']}\n\n"
                    f"HINT SQL (fiscal year filters removed):\n{best_sql_esc}\n"
                    f"{'='*60}\n"
                )
        target_table = state.get("target_table", "ras.tbl_ras_data")

        # Prompts
        system_prompt = SQL_SYSTEM_PROMPT.format(
            semantic_schema   = semantic_schema,
            few_shot_examples = few_shot_examples,
            target_table = target_table
        )

        user_prompt = (
            f"TASK:\n{state.get('current_query', '')}\n\n"
            f"REWRITTEN QUERY:\n{state.get('rewritten_query', '')}\n\n"
            f"FILTERS:\n{prompt_instruction}\n{filters_str}"
            f"{comparison_instruction}\n\n"
            f"METRIC:\n{state.get('metric')}\n\n"
            f"TOP N:\n{state.get('top_n')}"
            # f"{few_shot_enforcement}"
        )

        system_tokens = self.count_tokens(system_prompt)
        user_tokens   = self.count_tokens(user_prompt)
        total_tokens  = system_tokens + user_tokens
        # LLM call
        try:
            response: SQLContract = await self.llm.generate_structured(
                system_prompt = system_prompt,
                user_prompt   = user_prompt,
                schema_class  = SQLContract,
            )

            for q in response.queries:
                pure_sql = self.extract_first_sql(q.sql) 
                q.sql = self.clean_sql(pure_sql)

            return {"sql_queries": [q.model_dump() for q in response.queries]}

        except Exception as e:
            logger.error(f"SQL Agent Error: {str(e)}", exc_info=True)
            return {"final_response": "I encountered an error while generating the SQL query."}