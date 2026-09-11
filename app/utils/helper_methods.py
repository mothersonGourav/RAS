import re
from app.utils.logger import logger
from sqlglot import parse_one, exp
from collections import defaultdict
from typing import Any
import logging
import re
import json

# Columns where exact = match is correct (never use LIKE for these)
EXACT_MATCH_COLUMNS = {
    "fiscalyear", "year", "requisition_number", "requisition_type",
    "currency", "item_currency", "status", "classification",
    "advance_payment", "supply_chain_financing", "insourcing_flag",
    "is_event_id_created_in_ariba", "supplier_group", "fy",
}

# Columns where LOWER() + LIKE is correct
LIKE_MATCH_COLUMNS = {
    "site", "supplier", "region", "l1_category", "l2_category",
    "l3_category", "l4_category", "supplier_country", "site_country",
    "originator", "purchase_contact", "title", "item_name",
    "main_budget", "funding_through", "manufacturer", "customer",
    "sub_category", "part_name", "part_description",
}

def clean_filters_for_display(filters_used: dict) -> dict:
    cleaned = {}
    for key, value in (filters_used or {}).items():
        value_str = str(value)
        match = re.search(r"=\s*'(.+?)'", value_str)
        if match:
            display_value = match.group(1)
        else:
            display_value = re.sub(r"^[\w]+\.", "", value_str)
            display_value = re.sub(r"^[\w.]+\s*=\s*", "", display_value)
            display_value = display_value.strip("'\" ")
        clean_key = key.split(".")[-1].replace("_", " ").title()
        cleaned[clean_key] = display_value
    return cleaned

async def generate_summary(llm, user_query: str, record_count: int, data: list = None) -> str:
    if not data or record_count == 0:
        return "No matching records found."
    try:
        data_sample = data[:5] 
        system_prompt = (
            "You are a strict, factual data analyst translating raw database results into business insights. "
            "Your ONLY job is to answer the user's query using the provided Data Snippet. "
            "RULES:\n"
            "1. Return ONLY a single sentence (maximum 50 words).\n"
            "2. Be direct and factual. Extract the specific numbers, names, or metrics from the data.\n"
            "3. Do not hallucinate. If the answer isn't in the data, state what the data shows.\n"
            "4. Never mention 'SQL', 'database', 'JSON', or 'snippet'."
            "5. Do not overwrite the Data Snippet provided use the complete Data Snippet to generate the response."

        )
        user_prompt = (
            f"User Query: {user_query}\n"
            f"Total Records: {record_count}\n"
            f"Data Snippet: {json.dumps(data_sample, default=str)}\n\n"
            "Provide the single-sentence summary."
        )
        response = await llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )
        return response.strip()
    except Exception as e:  
        logger.error(f"Summary LLM error: {e}", exc_info=True)
        return f"Found {record_count} records matching your criteria."

def build_sql_fragment(col_ref: str, col_name: str, value: Any, route: str = None) -> str:
    if isinstance(value, dict):
        conditions = []
        for operator, val in value.items():
            if operator in [">=", "<=", ">", "<", "="]:
                safe_val = str(val).replace("'", "''")
                conditions.append(f"{col_ref} {operator} '{safe_val}'")
        if conditions:
            return f"({' AND '.join(conditions)})"
    safe_val  = str(value).replace("'", "''").lower()
    col_lower = col_name.lower() if col_name else ""
    if col_lower in EXACT_MATCH_COLUMNS:
        return f"LOWER({col_ref}) = '{safe_val}'"
    return f"LOWER({col_ref}) LIKE '%{safe_val}%'"

def is_date_bypass(v: str) -> bool:
    v = v.strip()
    if re.match(r"^\d+(\.\d+)?$", v):
        return True
    if re.match(r"^\d{4}-\d{4}$", v):
        return True
    if re.match(r"\d{1,4}[\-\/\.]\d{1,2}[\-\/\.]\d{1,4}", v):
        return True
    if (
        re.match(r"^[A-Za-z0-9]([A-Za-z0-9\-\_\/\.]*[A-Za-z0-9])?$", v)
        and re.search(r"[A-Za-z]", v)
        and re.search(r"[0-9]", v)
    ):
        return True
    return False

def normalize(s: str) -> str:
    return re.sub(r"[-_]+", " ", s).strip().lower()

def is_partial_match(user_input: str, db_value: str) -> bool:
    user_norm = normalize(user_input)
    db_norm   = normalize(db_value)
    return db_norm.startswith(user_norm) or user_norm in db_norm

def _extract_implicit_filters(sql_queries: list) -> dict:
    if not sql_queries:
        return {}
    sql = sql_queries[0].get("sql", "")
    where_match = re.search(
        r"WHERE\s+(.+?)(?:\s+GROUP BY|\s+ORDER BY|\s+LIMIT|\s+UNION|$)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if where_match:
        condition = re.sub(r"\s+", " ", where_match.group(1).strip())
        return {"Condition": condition}
    return {}

def resolve_fiscal_year_filter(value, table: str = "ras.tbl_ras_data") -> str:
    if isinstance(value, dict) and value.get("dynamic"):
        n = value.get("last_n", 3)
        if n == 1:
            return (
                f"fiscalyear = ("
                f"SELECT MAX(fiscalyear) FROM {table} "
                f"WHERE fiscalyear <> '2026-2027')"
            )
        else:
            return (
                f"fiscalyear IN ("
                f"SELECT DISTINCT fiscalyear FROM {table} "
                f"WHERE fiscalyear <> '2026-2027' "
                f"ORDER BY fiscalyear DESC LIMIT {n})"
            )
    return None


################ SQLGLOT ######################

def get_clean_column(node):
    """Strip SQL functions like LOWER() or UPPER() to return the base column name."""
    if isinstance(node, (exp.Lower, exp.Upper)):
        return node.this.sql()
    return node.sql()

def get_value(node):
    """Convert SQL AST node into Python value."""
    if node is None:
        return None
    if isinstance(node, exp.Literal):
        value = node.this
        if isinstance(value, str):
            value = value.strip("%")
        return value
    if isinstance(node, exp.Identifier):
        return node.this
    if isinstance(node, exp.Boolean):
        return node.this
    return node.sql()

def add_filter(result, column, operator, value):
    result[column].append({
        "operator": operator,
        "value": value
    })

def traverse(node, result):
    """Recursively traverse WHERE clause."""
    if node is None:
        return
    if isinstance(node, (exp.And, exp.Or)):
        traverse(node.left, result)
        traverse(node.right, result)
        return
    if isinstance(node, exp.EQ):
        add_filter(result, get_clean_column(node.left), "=", get_value(node.right))
        return
    if isinstance(node, exp.NEQ):
        add_filter(result, get_clean_column(node.left), "<>", get_value(node.right))
        return
    if isinstance(node, exp.GT):
        add_filter(result, get_clean_column(node.left), ">", get_value(node.right))
        return
    if isinstance(node, exp.GTE):
        add_filter(result, get_clean_column(node.left), ">=", get_value(node.right))
        return
    if isinstance(node, exp.LT):
        add_filter(result, get_clean_column(node.left), "<", get_value(node.right))
        return
    if isinstance(node, exp.LTE):
        add_filter(result, get_clean_column(node.left), "<=", get_value(node.right))
        return
    if isinstance(node, exp.Like):
        add_filter(result, get_clean_column(node.this), "LIKE", get_value(node.expression))
        return
    if isinstance(node, exp.ILike):
        add_filter(result, get_clean_column(node.this), "ILIKE", get_value(node.expression))
        return
    if isinstance(node, exp.In):
        values = [get_value(v) for v in node.expressions]
        add_filter(result, get_clean_column(node.this), "IN", values)
        return
    if isinstance(node, exp.Not) and isinstance(node.this, exp.In):
        values = [get_value(v) for v in node.this.expressions]
        add_filter(result, get_clean_column(node.this.this), "NOT IN", values)
        return
    if isinstance(node, exp.Between):
        value = [get_value(node.args["low"]), get_value(node.args["high"])]
        add_filter(result, get_clean_column(node.this), "BETWEEN", value)
        return
    if isinstance(node, exp.Is):
        return
    if isinstance(node, exp.Not):
        if isinstance(node.this, exp.Is):
            return
    for child in node.args.values():
        if isinstance(child, list):
            for c in child:
                if isinstance(c, exp.Expression):
                    traverse(c, result)
        elif isinstance(child, exp.Expression):
            traverse(child, result)

def extract_ast_filters(sql: str) -> dict:
    try:
        tree = parse_one(sql, read="postgres")
        where = tree.find(exp.Where)
        result = defaultdict(list)
        if where:
            traverse(where.this, result)
        return dict(result)
    except Exception as e:
        logger.error(f"SQLGlot parsing failed for query: {sql} | Error: {str(e)}")
        return {}

def format_ast_filters(ast_filters: dict) -> dict:
    formatted_filters = {}
    for key, conditions in (ast_filters or {}).items():
        if not conditions:
            continue
        clean_key = key.split(".")[-1].lower()
        clean_key = re.sub(r'^(?:lower|upper)\((.*?)\)$', r'\1', clean_key)
        if not isinstance(conditions, list):
            formatted_filters[clean_key] = str(conditions)
            continue
        extracted_values = []
        for c in conditions:
            if isinstance(c, dict):
                val = c.get("value")
            else:
                val = c
            if isinstance(val, list):
                extracted_values.extend([str(v) for v in val])
            elif val is not None:
                extracted_values.append(str(val))
        if extracted_values:
            formatted_filters[clean_key] = ", ".join(extracted_values)

    return formatted_filters