from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

from app.config import settings


def get_embedder() -> OpenAIEmbeddings:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot embed documents for RAG.")
    return OpenAIEmbeddings(api_key=settings.openai_api_key, model=settings.openai_embedding_model)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    embedder = get_embedder()
    # LangChain returns list[list[float]]
    return await embedder.aembed_documents(texts)


async def embed_query(text: str) -> list[float]:
    embedder = get_embedder()
    return await embedder.aembed_query(text)

