from __future__ import annotations

from openai import AzureOpenAI

from skill_reviewer.config import ReviewerConfig


def build_openai_client(config: ReviewerConfig) -> AzureOpenAI:
    if config.api_key:
        return AzureOpenAI(
            api_key=config.api_key,
            api_version=config.api_version,
            azure_endpoint=config.azure_endpoint,
        )

    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as exc:
        raise RuntimeError(
            "API key authentication is not configured and azure-identity is unavailable."
        ) from exc

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        config.token_scope,
    )
    return AzureOpenAI(
        api_version=config.api_version,
        azure_endpoint=config.azure_endpoint,
        azure_ad_token_provider=token_provider,
    )
