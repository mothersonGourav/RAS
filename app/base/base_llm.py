from abc import ABC, abstractmethod
from typing import Type, Any


class BaseLLM(ABC):
    """Interface for all LLM providers (OpenAI, Anthropic, etc.)"""

    @abstractmethod
    async def generate_structured(self, system_prompt: str, user_prompt: str, schema_class: Type[Any],
                                  history: list = None) -> Any:
        """Returns data strictly conforming to the provided Pydantic schema."""
        pass

    @abstractmethod
    async def generate_text(self, system_prompt: str, user_prompt: str, history: list = None) -> str:
        """Returns standard text response."""
        pass