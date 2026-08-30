"""Build model providers used by the research agents."""

from typing import Any, Literal

from botocore.config import Config
from strands.models import BedrockModel, Model
from strands.models.openai import OpenAIModel

ModelProvider = Literal["openrouter", "bedrock"]


def build_model(
    provider: ModelProvider,
    model_id: str,
    timeout_seconds: int,
    max_tokens: int,
    *,
    temperature: float | None = 0,
    client_max_retries: int = 0,
    model_parameters: dict[str, Any] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    region_name: str | None = None,
) -> Model:
    """Build a Strands model for a supported provider.

    Args:
        provider: OpenRouter or Amazon Bedrock model provider.
        model_id: Provider model identifier.
        timeout_seconds: Request timeout in seconds.
        max_tokens: Maximum output tokens per model call.
        temperature: Sampling temperature.
        client_max_retries: Retries performed by the provider client.
        model_parameters: Optional provider-specific model parameters.
        api_key: API key required by an OpenAI-compatible endpoint.
        base_url: Base URL required by an OpenAI-compatible endpoint.
        region_name: AWS region used by Amazon Bedrock.

    Returns:
        Configured Strands model.

    Raises:
        ValueError: If required provider settings are missing.
    """
    if not model_id.strip():
        raise ValueError("model_id is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")
    if client_max_retries < 0:
        raise ValueError("client_max_retries cannot be negative")

    params = dict(model_parameters or {})
    if temperature is not None:
        params["temperature"] = temperature
    params["max_tokens"] = max_tokens
    if provider == "openrouter":
        if not api_key or not base_url:
            raise ValueError("OpenAI-compatible models require api_key and base_url")
        return OpenAIModel(
            client_args={
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout_seconds,
                "max_retries": client_max_retries,
            },
            model_id=model_id,
            params=params,
        )
    if provider == "bedrock":
        if not region_name:
            raise ValueError("Bedrock models require region_name")
        return BedrockModel(
            region_name=region_name,
            boto_client_config=Config(
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": client_max_retries},
            ),
            model_id=model_id,
            **params,
        )
    raise ValueError(f"Unsupported model provider: {provider}")
