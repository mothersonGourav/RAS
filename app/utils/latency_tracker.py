import time
from functools import wraps
from app.utils.logger import logger

def measure_latency(func):
    """
    A decorator that measures the execution time of asynchronous functions.It automatically logs the class name, function name, and latency in milliseconds.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = await func(*args, **kwargs)
        
        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000
        if args and hasattr(args[0], '__class__'):
            target_name = f"{args[0].__class__.__name__}.{func.__name__}"
        else:
            target_name = func.__name__            
        logger.info(f"[LATENCY] {target_name} completed in {latency_ms:.2f} ms")
        
        return result
    return wrapper