import os
from typing import Type, Any
from app.base.base_llm import BaseLLM
from langchain_openai import AzureChatOpenAI 
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# Consider renaming to AzureOpenAIClient for clarity
class AzureOpenAIClient(BaseLLM): 
    def __init__(self, temperature: float = 0.0):
        # 2. Map the Azure-specific parameters to your environment variables
        self.llm = AzureChatOpenAI(
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            temperature=temperature
        )

    # Generates the response based on the user prompt by core generate method 
    async def generate(self, system_prompt: str, user_prompt: str, history: list = None) -> str:
        messages = [("system", system_prompt)]
        if history:
            messages.append(MessagesPlaceholder(variable_name="history"))
        messages.append(("human", "{query}"))
        
        prompt = ChatPromptTemplate.from_messages(messages)
        chain = prompt | self.llm
        
        result = await chain.ainvoke({
            "query": user_prompt,
            "history": history or []
        })
        return result.content.strip()

    # Generate the raw text based response only
    async def generate_text(self, system_prompt: str, user_prompt: str, history: list = None) -> str:
        return await self.generate(
            system_prompt,
            user_prompt,
            history
        )

    # Generate response based on the pydantic schema
    async def generate_structured(self, system_prompt: str, user_prompt: str, schema_class: Type[Any], history: list = None) -> Any:

        structured_llm = self.llm.with_structured_output(schema_class, method="function_calling")
        messages = [("system", system_prompt)]
        if history:
            messages.append(MessagesPlaceholder(variable_name="history"))
        messages.append(("human", "{query}"))
        
        prompt = ChatPromptTemplate.from_messages(messages)
        chain = prompt | structured_llm
        
        return await chain.ainvoke({
            "query": user_prompt,
            "history": history or []
        })
    
    # Invoke the process
    async def invoke(self, prompt: str) -> str:
        return await self.generate(
            "You are an expert SQL assistant.",
            prompt
        )