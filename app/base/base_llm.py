from abc import ABC
from abc import abstractmethod


class BaseLLM(ABC):

    @abstractmethod
    async def generate(self,system_prompt: str,user_prompt: str, history=None):
        pass

    @abstractmethod
    async def generate_structured(self,system_prompt: str,user_prompt: str,schema_class,history=None,):
        pass

    @abstractmethod
    async def generate_text(self, system_prompt: str, user_prompt: str, history=None):
        pass