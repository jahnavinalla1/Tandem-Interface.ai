"""Explicit provider selection; never silently switch providers or billing accounts."""

from tandem.config import Settings
from tandem.discovery.gemini import GeminiProvider
from tandem.discovery.provider import DiscoveryProvider, OpenAIResponsesProvider


def create_discovery_provider(config: Settings) -> DiscoveryProvider:
    if config.discovery_provider == "gemini":
        return GeminiProvider(config.gemini_api_key, config.discovery_model or "gemini-3.6-flash")
    if config.discovery_provider == "openai":
        return OpenAIResponsesProvider(config.openai_api_key, config.discovery_model or "gpt-5")
    raise ValueError("DISCOVERY_PROVIDER must be gemini or openai")
