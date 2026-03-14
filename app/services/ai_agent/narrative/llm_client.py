"""
LLM client — wrappers around OpenAI text and multimodal APIs.

Centralizes:
  - API key management (from app.config)
  - Timeout configuration
  - Model selection
  - Basic request/response logging
"""

from langchain_openai import ChatOpenAI
import httpx

from app.config import settings
from app.config.logging_config import get_logger

logger = get_logger(__name__)


def get_chat_llm(*, temperature: float = 0.3) -> ChatOpenAI:
    """Create a LangChain Chat LLM (non-streaming) using configured settings."""
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in .env or as an environment variable."
        )
    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        temperature=temperature,
        timeout=settings.openai_timeout_seconds,
        max_tokens=settings.openai_max_tokens,
        streaming=False,
    )


def _log_empty_completion_diagnostics(msg: object) -> None:
    """Emit extra diagnostics when the model returns an empty content payload."""
    raw_content = getattr(msg, "content", None)
    response_metadata = getattr(msg, "response_metadata", None) or {}
    additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
    usage_metadata = getattr(msg, "usage_metadata", None) or {}
    tool_calls = getattr(msg, "tool_calls", None) or []
    invalid_tool_calls = getattr(msg, "invalid_tool_calls", None) or []

    logger.warning(
        "llm_request_empty_completion",
        raw_content_type=type(raw_content).__name__,
        raw_content_preview=repr(raw_content)[:1000],
        response_metadata=response_metadata,
        response_metadata_keys=sorted(response_metadata.keys())
        if isinstance(response_metadata, dict)
        else [],
        additional_kwargs=additional_kwargs,
        additional_kwargs_keys=sorted(additional_kwargs.keys())
        if isinstance(additional_kwargs, dict)
        else [],
        usage_metadata=usage_metadata,
        tool_calls=tool_calls,
        invalid_tool_calls=invalid_tool_calls,
    )


async def invoke_text(*, system_message: str, user_message: str, temperature: float = 0.3) -> str:
    """Invoke the chat model and return plain text content."""
    llm = get_chat_llm(temperature=temperature)
    logger.info(
        "llm_request_started",
        model=settings.openai_model,
        system_len=len(system_message),
        user_len=len(user_message),
    )
    msg = await llm.ainvoke([("system", system_message), ("user", user_message)])
    content = getattr(msg, "content", "") or ""
    if len(content) == 0:
        _log_empty_completion_diagnostics(msg)
    logger.info(
        "llm_request_completed",
        model=settings.openai_model,
        completion_len=len(content),
    )
    return content


async def invoke_multimodal_text(
    *,
    system_message: str,
    user_message: str,
    image_base64: str,
    image_mime_type: str = "image/png",
    temperature: float = 0.2,
) -> str:
    """Invoke OpenAI chat completions with text + image and return plain text content."""
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in .env or as an environment variable."
        )

    image_url = f"data:{image_mime_type};base64,{image_base64}"
    payload = {
        "model": settings.openai_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_message},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    logger.info(
        "llm_multimodal_request_started",
        model=settings.openai_model,
        system_len=len(system_message),
        user_len=len(user_message),
        image_mime_type=image_mime_type,
    )
    async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    content = (
        (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        if isinstance(data, dict)
        else ""
    )
    logger.info(
        "llm_multimodal_request_completed",
        model=settings.openai_model,
        completion_len=len(content),
    )
    return str(content)

