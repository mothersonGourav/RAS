import json
import os
from app.utils.logger import logger
from app.utils.config import Config
from collections import defaultdict

class RASSchemaManager:
    def __init__(self, filepath: str = Config.RAS_SCHEMA_FILE_PATH):
        self.filepath = filepath
        self.raw_data = self.load_schema()
        self.schema   = self.build_schema()

    def get_raw_schema(self):
        """Return the raw schema JSON. Used by IntentAgent._build_alias_map()."""
        return self.raw_data

    def load_schema(self):
        if not os.path.exists(self.filepath):
            logger.error(f"Schema file not found at {self.filepath}")
            return {}

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
                return schema_data
        except Exception as e:
            logger.error(f"Failed to load schema: {str(e)}", exc_info=True)
            return {}

    def build_schema(self):
        if not self.raw_data:
            return {}

        schema = {
            "domain": "Procurement and Spend Analytics",
            "tables": [],
            "join_registry": self.raw_data.get("join_registry", {}) 
        }

        for table_info in self.raw_data.get("tables", []):
            table_name  = table_info.get("table_name", "unknown_table")
            if table_name != "tbl_ras_data":
                continue
            schema_name = table_info.get("schema_name", "ras")
            columns     = []

            for col in table_info.get("columns", []):
                columns.append({
                    "name": col.get("name", ""),
                    "description": col.get("description", ""),
                    "aliases": col.get("aliases", []),
                    "data_type": col.get("type", "TEXT")
                })

            schema["tables"].append({
                "schema_name": schema_name,
                "table_name": table_name,
                "columns": columns,
                "primary_keys": table_info.get("primary_key", []),
                "description": table_info.get("description", "")
            })

        logger.info(f"Loaded schema successfully. Tables: {len(schema['tables'])}")

        return schema

    def find_column_by_alias(self, label: str):
        if not self.schema:
            return None, None, None

        target = label.lower().strip()

        for table in self.schema.get("tables", []):
            table_name = table.get("table_name")

            for col in table.get("columns", []):
                name = col.get("name", "").lower()
                aliases = [a.lower() for a in col.get("aliases", [])]

                if target == name or target in aliases:
                    return table_name, col.get("name"), col

        return None, None, None

    def get_intent_context(self) -> str:
        if not self.schema:
            return "No schema available."

        context = [f"DOMAIN: {self.schema.get('domain')}"]

        for table in self.schema.get("tables", []):
            context.append(f"TABLE: {table.get('table_name')}")
            context.append(f"DESCRIPTION: {table.get('description', '')}")

            for col in table.get("columns", []):
                aliases = ", ".join(col.get("aliases", []))
                alias_text = f" | aliases=[{aliases}]" if aliases else ""
                context.append(f"- {col.get('name')}: {col.get('description')}{alias_text}")

        return "\n".join(context)

    def get_sql_context(self) -> str:
        if not self.schema:
            return "No schema available."

        context = []

        # Print Table & Column Definitions
        for table in self.schema.get("tables", []):
            schema_name = table.get("schema_name", "ras")
            table_name  = table.get("table_name")

            context.append(f"TABLE: {schema_name}.{table_name}")
            columns = [f"{col.get('name')} ({col.get('data_type')})" for col in table.get("columns", [])]
            context.append(f"COLUMNS: {', '.join(columns)}")
            context.append("")

        # Print Strict Join Rules from the Registry
        join_registry = self.schema.get("join_registry", {})
        if join_registry:
            context.append("STRICT JOIN RULES")
            context.append("If the query requires data from multiple tables, you MUST use these exact JOIN conditions:\n")
            
            for source_table, joins in join_registry.items():
                for join_def in joins:
                    ref_table  = join_def.get("references_table")
                    source_col = join_def.get("column")
                    ref_col    = join_def.get("references_column")
                    rule = f"- To join {source_table} and {ref_table}, use: ON {source_table}.{source_col} = {ref_table}.{ref_col}"
                    context.append(rule)

        return "\n".join(context)

class HarmoniaSchemaManager:
    """Loads a flat Semantic Schema JSON and dynamically reconstructs it into an Agent-ready hierarchy."""
    
    def __init__(self, filepath: str = Config.HARMONIA_SCHEMA_FILE_PATH):
        self.filepath = filepath
        self.raw_data = self.load_schema()
        self.schema = self.build_nested_schema()
        
    # Loads the JSON Schema
    def load_schema(self) -> list:

        if not os.path.exists(self.filepath):
            logger.error(f"Schema file not found at {self.filepath}")
            return []            
        try:
            with open(self.filepath, 'r', encoding="utf-8") as f:
                schema_data = json.load(f)
                return schema_data
        except Exception as e:
            logger.error(f"Failed to parse Semantic Schema JSON: {e}")
            return []

    # Takes the flat list of fields and builds the nested dictionary the LLMs expect. Translates 'field_name' -> 'column_name' and 'alias' -> 'aliases'.
    def build_nested_schema(self) -> dict:

        if not self.raw_data:
            return {}
        join_registry = {
            "employee_inouttime": [
                {"column": "GlobalUserID", "references_table": "employee_master", "references_column": "GlobalUserId"}
            ],
            "employee_leavebalance": [
                {"column": "GlobalUserID", "references_table": "employee_master", "references_column": "GlobalUserId"}
            ],
            "employee_leavetaken": [
                {"column": "GlobalUserID", "references_table": "employee_master", "references_column": "GlobalUserId"}
            ],
            "manager_master": [
                {"column": "ManagerId", "references_table": "employee_master", "references_column": "GlobalUserId"},
                {"column": "ReporteeID", "references_table": "employee_master", "references_column": "GlobalUserId"}
            ]
        }
        tables_map = defaultdict(list)
        for field in self.raw_data:
            table_name = field.get("table_name", "unknown_table")
            normalized_col = {
                "column_name": field.get("field_name"),
                "description": field.get("description"),
                "aliases": field.get("alias", []),
                "data_type": "TEXT" 
            }
            tables_map[table_name].append(normalized_col)
        nested_schema = {
            "domain": "HR and Payroll",
            "tables": []
        }
        for table_name, columns in tables_map.items():
            table_def = {
                "table_name": table_name,
                "columns": columns,
                "primary_keys": [],
                "foreign_key_candidates": join_registry.get(table_name, [])
            }
            nested_schema["tables"].append(table_def)
        logger.info(f"Rebuilt schema hierarchy in memory. Loaded {len(nested_schema['tables'])} tables.")
        return nested_schema
            
    # match columns using the alias provided in the JSON 
    def find_column_by_alias(self, label: str) -> tuple[str, str, dict] | tuple[None, None, None]:

        if not self.schema:
            return None, None, None
        target_label = label.lower().strip()
        fallback_match = None
        for table in self.schema.get("tables", []):
            table_name = table.get("table_name")
            pks = [pk.lower() for pk in table.get("primary_keys", [])]
            for col in table.get("columns", []):
                exact_name = col.get("column_name", "")
                aliases = [a.lower() for a in col.get("aliases", [])]
                if target_label == exact_name.lower() or target_label in aliases:
                    if exact_name.lower() in pks:
                        return table_name, exact_name, col
                    if not fallback_match:
                        fallback_match = (table_name, exact_name, col) 
        if fallback_match:
            return fallback_match
            
        return None, None, None

    # returns a vocabulary-rich schema for intent extraction used by  intent agent
    def get_intent_context(self) -> str:

        if not self.schema:
            return "No semantic schema available."
        context = [f"DOMAIN: {self.schema.get('domain', '')}\n"]

        for table in self.schema.get("tables", []):
            context.append(f"TABLE: {table.get('table_name')}")
            for col in table.get("columns", []):
                c_name = col.get("column_name")
                c_desc = col.get("description", "")
                c_aliases = ", ".join(col.get("aliases", []))
                alias_str = f" | Aliases: [{c_aliases}]" if c_aliases else ""
                context.append(f"  - {c_name}: {c_desc}{alias_str}")
            context.append("")
        return "\n".join(context)

    # Returns a strictly structured schema for SQL generation used by SQL Agent
    def get_sql_context(self) -> str:

        if not self.schema:
            return "No semantic schema available."
        context = []
        for table in self.schema.get("tables", []):
            table_name = table.get("table_name")
            context.append(f"TABLE: {table_name}")
            for fk in table.get("foreign_key_candidates", []):
                context.append(f"  -> JOIN HINT: {fk['column']} joins with {fk['references_table']}.{fk['references_column']}")
            col_strings = []
            for col in table.get("columns", []):
                col_strings.append(f"{col.get('column_name')} ({col.get('data_type')})")
            context.append(f"  -> COLUMNS: {', '.join(col_strings)}")
            context.append("")
        return "\n".join(context)