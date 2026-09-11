from app.base.base_llm import BaseLLM
from app.models.pydantic_models import GuardrailSchema
from app.prompts.prompt_registry import GUARDRAIL_SYSTEM_PROMPT
from app.utils.logger import logger

class GuardrailAgent:
    def __init__(self, llm_client: BaseLLM):
        self.llm = llm_client

    async def __call__(self, user_query: str) -> GuardrailSchema:
        logger.info("Guardrail Agent: Scanning query...")
        try:
            response: GuardrailSchema = await self.llm.generate_structured(
                system_prompt=GUARDRAIL_SYSTEM_PROMPT,
                user_prompt=user_query,
                schema_class=GuardrailSchema
            )
            logger.info(f"Guardrail Agent: Safe={response.is_safe}")
            return response
        except Exception as e:
            logger.error(f"Guardrail Agent Error: {str(e)}", exc_info=True)
            return GuardrailSchema(is_safe=False, reason="Internal guardrail validation failed.")