import hashlib
import json
import redis.asyncio as redis
from typing import Optional, Dict, Any, List
from app.utils.config import Config
from app.utils.logger import logger

class RedisUserCache:
    def __init__(self):
        self.pool: Optional[redis.Redis] = None
        self.ttl = 604800
        self.history_ttl = 604800

    # Initialize Redis Connection
    async def connect(self):
        self.pool = redis.from_url(
            Config.REDIS_URL, 
            encoding="utf-8", 
            decode_responses=True,
            max_connections=20,
            protocol=2
        )
        logger.info("[Redis] Async Connection Pool established.")

    # Closing the Redis Connection
    async def close(self):
        if self.pool:
            await self.pool.aclose()

    async def acquire_lock(self, user_id: str, request_id: str, timeout: int = 25) -> bool:
        key = f"lock:usr:{user_id}"
        acquired = await self.pool.set(key, request_id, nx=True, ex=timeout)
        return bool(acquired)

    async def release_lock(self, user_id: str, request_id: str):
        key = f"lock:usr:{user_id}"
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.pool.eval(lua_script, 1, key, request_id)

    # Generate user based isolated key for exact response caching
    def generate_key(self, user_id: str, language: str, raw_query: str) -> str:
        clean_q = raw_query.strip().lower()
        q_hash = hashlib.sha256(clean_q.encode('utf-8')).hexdigest()
        return f"usr:{user_id}:lang:{language.lower()}:q:{q_hash}"

    # Generate isolated key specifically for chat history
    def get_history_key(self, user_id: str, session_id: str) -> str:
        return f"usr:{user_id}:sess:{session_id}:chat_history"

    def get_history_key_ras(self, user_id: str) -> str:
        return f"usr:{user_id}:chat_history"

    async def get_chat_history(self, user_id: str, session_id: str, limit: int = 50) -> List[Dict[str, str]]:
        key = self.get_history_key(user_id, session_id)
        try:
            data = await self.pool.lrange(key, -limit, -1)
            if data:
                return [json.loads(msg) for msg in data]
            return []
        except Exception as e:
            logger.error(f"[Redis Get History Error] Err: {e}")
            return []
        
    async def get_chat_history_ras(self, user_id: str, limit: int = 50) -> List[Dict[str, str]]:
        key = self.get_history_key_ras(user_id)
        try:
            data = await self.pool.lrange(key, -limit, -1)
            if data:
                return [json.loads(msg) for msg in data]
            return []
        except Exception as e:
            logger.error(f"[Redis Get History Error] Err: {e}")
            return []
        
    async def append_chat_message_ras(self, user_id: str, role: str, content: str):
        key = self.get_history_key_ras(user_id)
        message = {"role": role, "content": content}
        try:
            async with self.pool.pipeline(transaction=True) as pipe:
                pipe.rpush(key, json.dumps(message))
                pipe.ltrim(key, -50, -1)
                pipe.expire(key, self.history_ttl)
                await pipe.execute()
        except Exception as e:
            logger.error(f"[Redis Append Error]: {e}")

    async def append_chat_message(self, user_id: str,role: str, content: str):
        key = self.get_history_key(user_id)
        message = {"role": role, "content": content}
        try:
            async with self.pool.pipeline(transaction=True) as pipe:
                pipe.rpush(key, json.dumps(message))
                pipe.ltrim(key, -50, -1)
                pipe.expire(key, self.history_ttl)
                await pipe.execute()
            logger.info(f"[Redis SAVE] History appended for user {user_id} ({role}).")
        except Exception as e:
            logger.error(f"[Redis Append Error] Failed to write history: {e}")

    # Returns the cached response based on user id saved in redis cache
    async def get_cached_response(self, user_id: str,language: str, query: str) -> Optional[Dict[str, Any]]:
        key = self.generate_key(user_id,language, query)
        try:
            data = await self.pool.get(key)
            if data:
                logger.info(f"[Cache HIT] User: {user_id} | Key: {key}")
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"[Redis Get Error] Falling back to LLM. Err: {e}")
            return None

    # Save the raw response in redis 
    async def save_response(self, user_id: str,language: str, query: str, final_response: str,data:Any):
        key = self.generate_key(user_id,language, query)
        payload = {
            "user_id": user_id,
            "query": query,
            "language": language,
            "final_response": final_response,
            "raw_db_results": data
        }
        try:
            await self.pool.setex(key, self.ttl, json.dumps(payload, default=str)) 
            logger.info(f"[Cache SAVE] User: {user_id} | Key: {key}")
        except Exception as e:
            logger.error(f"[Redis Set Error] Failed to write cache: {e}")

    def get_semantic_key(self, user_id: str) -> str:
        return f"usr:{user_id}:semantic_state"

    async def get_semantic_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        key = self.get_semantic_key(user_id)
        data = await self.pool.get(key)
        return json.loads(data) if data else None

    async def save_semantic_state(self, user_id: str, state_payload: dict):
        key = self.get_semantic_key(user_id)
        await self.pool.setex(key, self.ttl, json.dumps(state_payload, default=str))

    def get_ui_history_key(self, user_id: str) -> str:
        return f"usr:{user_id}:ui_history"

    # Fetches the complete rich UI history for the frontend.
    async def get_ui_history(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        key = self.get_ui_history_key(user_id)
        try:
            data = await self.pool.lrange(key, -limit, -1)
            if data:
                return [json.loads(msg) for msg in data]
            return []
        except Exception as e:
            logger.error(f"[Redis Get UI History Error] Err: {e}")
            return []
        
    # Appends the entire response object (data, insights, sql) for frontend rendering.
    async def append_ui_history(self, user_id: str, payload: dict):
        key = self.get_ui_history_key(user_id)
        try:
            async with self.pool.pipeline(transaction=True) as pipe:

                pipe.rpush(key, json.dumps(payload, default=str))
                pipe.ltrim(key, -50, -1)
                pipe.expire(key, getattr(self, "history_ttl", 604800))
                await pipe.execute()
            logger.info(f"[Redis SAVE] UI History appended for user {user_id}.")
        except Exception as e:
            logger.error(f"[Redis Append UI Error]: {e}")

    async def set_cache(self, user_id: str, module: str, topic: str, language: str, query: str, data: Any):
        try:
            await self.pool.setex(query, self.ttl, json.dumps(data, default=str))
            logger.info(f"[Redis SAVE] Cache saved for module {module} with ID {query}")
        except Exception as e:
            logger.error(f"[Redis set_cache Error] Failed to write cache: {e}")