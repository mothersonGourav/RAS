import re
import asyncpg
import pandas as pd
from urllib.parse import quote_plus
from typing import List, Dict, Any, Tuple
from app.base.base_rdbms import BaseRDBMS
from app.utils.logger import logger
from app.utils.config import Config


class PostgresClient(BaseRDBMS):
    def __init__(self):
        user     = quote_plus(str(Config.POSTGRES_CONFIG["user"]))
        password = quote_plus(str(Config.POSTGRES_CONFIG["password"]))
        host     = Config.POSTGRES_CONFIG["host"]
        port     = Config.POSTGRES_CONFIG["port"]
        database = quote_plus(str(Config.POSTGRES_CONFIG["database"]))
        self.connection_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        self.pool = None

    async def _get_pool(self):
        if not self.pool:
            logger.info("Initializing PostgreSQL connection pool...")
            self.pool = await asyncpg.create_pool(dsn=self.connection_url)
        return self.pool
    
    async def connect(self):
        if not self.pool:
            logger.info("Initializing PostgreSQL connection pool...")
            try:
                self.pool = await asyncpg.create_pool(
                    dsn=self.connection_url,
                    min_size=5,
                    max_size=20
                )
                logger.info("PostgreSQL pool initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to connect to Database on startup: {e}")
                self.pool = None

    def extract_arg(self, s: str, start: int) -> Tuple[str, int]:

        depth = 0
        arg   = []
        i     = start
        while i < len(s):
            ch = s[i]
            if ch == "(":
                depth += 1
                arg.append(ch)
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
                arg.append(ch)
            elif ch == "," and depth == 0:
                break
            else:
                arg.append(ch)
            i += 1

        return "".join(arg), i

    def fix_round(self, sql: str) -> str:

        result = []
        i      = 0
        upper  = sql.upper()
        while i < len(sql):
            if upper[i:i+6] == "ROUND(":
                i += 6
                expr, i = self.extract_arg(sql, i)
                if i < len(sql) and sql[i] == ",":
                    i += 1
                    prec, i = self.extract_arg(sql, i)
                    prec_str = prec.strip()
                    if prec_str.isdigit():
                        result.append(f"ROUND(({expr.strip()})::NUMERIC, {prec_str})")
                    else:
                        result.append(f"ROUND({expr}, {prec})")
                else:
                    result.append(f"ROUND({expr}")
                i += 1
            else:
                result.append(sql[i])
                i += 1

        return "".join(result)

    
    def strip_sql_comments(self, sql: str) -> str:
    
        sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.DOTALL)
        sql = re.sub(r'--[\s\-─=]+', ' ', sql)
        cleaned_lines = []
        for line in sql.split("\n"):
            if "--" in line:
                line = line.split("--")[0]
            if line.strip():
                cleaned_lines.append(line)

        return " ".join(cleaned_lines)

    def fix_sql(self, sql: str) -> str:
        sql_no_comments = self.strip_sql_comments(sql)
        return self.fix_round(sql_no_comments)

    # Executes the SQL query against postgres DB
    async def execute_query(self, query: str, *args) -> List[Dict[str, Any]]:

        fixed_query = query
        try:
            pool = await self._get_pool()
            fixed_query = self.fix_sql(query)
            print("ORIGINAL SQL :",query)
            print("FIXED SQL :",fixed_query)

            statements = [s.strip() for s in fixed_query.split(";") if s.strip()]
            for idx, stmt in enumerate(statements):
                pass

            if not statements:
                logger.error("[PostgresClient] No executable statements found after SQL fixing.")
                return []
            
            async with pool.acquire() as conn:

                if len(statements) == 1:

                    records = await conn.fetch(statements[0], *args)
                    result  = [dict(r) for r in records]
                    return result

                else:
                    all_results = []
                    for idx, stmt in enumerate(statements):
                        logger.info(f"Executing statement {idx}:\n{stmt[:120]}...")
                        records = await conn.fetch(stmt)
                        batch   = [dict(r) for r in records]
                        all_results.extend(batch)
                    return all_results

        except Exception as e:
            logger.error(
                f"PostgreSQL Query Execution Failed: {e}\n",
                exc_info=True,
            )
            return []

    # Builds the dataframe of the DB response.
    async def get_dataframe(self, query: str) -> pd.DataFrame:

        rows = await self.execute_query(query)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    # Fetch the schems of the given table
    async def get_schema(self) -> str:

        logger.info("Fetching PostgreSQL schema...")
        query = """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema='harmonia'
        ORDER BY table_name, ordinal_position;
        """
        rows = await self.execute_query(query)
        return "\n".join(
            f"{r['table_schema']}.{r['table_name']}.{r['column_name']} ({r['data_type']})"
            for r in rows
        )

    # Add this method to your PostgresClient class
    # async def get_user_role(self, user_id: str) -> str:
    #     """Securely determines the user's RBAC role via a database lookup."""
    #     if not user_id:
    #         return "employee" # Safest default

    #     # 1. Admin Override Check (Add your specific admin IDs here)
    #     admin_ids = ["ADMIN_001", "M10000001"] 
    #     if user_id in admin_ids:
    #         return "admin"

    #     # 2. Manager Check: Does this ID exist as a manager in manager_master?
    #     sql = "SELECT 1 FROM manager_master WHERE ManagerId = $1 LIMIT 1;"
        
    #     try:
    #         pool = await self._get_pool()
    #         async with pool.acquire() as conn:
    #             is_manager = await conn.fetchval(sql, user_id)
    #             if is_manager:
    #                 return "manager"
    #     except Exception as e:
    #         from app.utils.logger import logger
    #         logger.error(f"Failed to fetch user role for {user_id}: {e}")

    #     # 3. Default Fallback
    #     return "employee"

    # Create caches for the schema returned from DB.
    async def get_schema_cache(self) -> Dict[str, str]:

        logger.info("Building PostgreSQL schema cache.")
        query = """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema='harmonia';
        """
        rows  = await self.execute_query(query)
        cache = {}
        for row in rows:
            cache[row["column_name"].lower()] = row["table_name"]
        return cache
    
    async def close(self):
        if self.pool:
            await self.pool.close()
    


