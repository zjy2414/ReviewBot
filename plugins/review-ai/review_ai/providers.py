"""Model provider definitions for the Review AI tool.

Review AI is not tied to a single vendor. Each provider describes the wire
protocol to use, the default endpoint, the default model, and the environment
variable that holds its API key.
"""

from __future__ import annotations

import os

from dataclasses import dataclass


#: The OpenAI-compatible chat completions protocol.
PROTOCOL_OPENAI = 'openai'

#: The Anthropic Messages protocol.
PROTOCOL_ANTHROPIC = 'anthropic'

#: A generic environment variable that overrides any provider-specific key.
GENERIC_API_KEY_ENV = 'REVIEW_AI_API_KEY'

#: An optional environment variable overriding the endpoint for any provider.
BASE_URL_ENV = 'REVIEW_AI_BASE_URL'

#: An optional environment variable overriding the model for any provider.
MODEL_ENV = 'REVIEW_AI_MODEL'


@dataclass(frozen=True)
class Provider:
    """A model provider and the defaults used when talking to it.

    ``json_mode`` is only a hint: clients retry without it if the server
    rejects the request.
    """

    key: str
    label: str
    protocol: str
    base_url: str
    default_model: str
    api_key_env: str = ''
    requires_api_key: bool = True
    json_mode: bool = True


#: All supported providers, in display order.
PROVIDERS: dict[str, Provider] = {
    'deepseek': Provider(
        key='deepseek',
        label='DeepSeek',
        protocol=PROTOCOL_OPENAI,
        base_url='https://api.deepseek.com',
        default_model='deepseek-flash',
        api_key_env='DEEPSEEK_API_KEY',
    ),
    'openai': Provider(
        key='openai',
        label='OpenAI',
        protocol=PROTOCOL_OPENAI,
        base_url='https://api.openai.com/v1',
        default_model='gpt-4o-mini',
        api_key_env='OPENAI_API_KEY',
    ),
    'qwen': Provider(
        key='qwen',
        label='Alibaba Cloud Qwen (DashScope compatible mode)',
        protocol=PROTOCOL_OPENAI,
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        default_model='qwen-plus',
        api_key_env='DASHSCOPE_API_KEY',
    ),
    'moonshot': Provider(
        key='moonshot',
        label='Moonshot (Kimi)',
        protocol=PROTOCOL_OPENAI,
        base_url='https://api.moonshot.cn/v1',
        default_model='moonshot-v1-32k',
        api_key_env='MOONSHOT_API_KEY',
    ),
    'zhipu': Provider(
        key='zhipu',
        label='Zhipu AI (GLM)',
        protocol=PROTOCOL_OPENAI,
        base_url='https://open.bigmodel.cn/api/paas/v4',
        default_model='glm-4-plus',
        api_key_env='ZHIPUAI_API_KEY',
    ),
    'ollama': Provider(
        key='ollama',
        label='Ollama (local)',
        protocol=PROTOCOL_OPENAI,
        base_url='http://localhost:11434/v1',
        default_model='qwen2.5-coder:14b',
        # Local models need no credentials.
        requires_api_key=False,
    ),
    'anthropic': Provider(
        key='anthropic',
        label='Anthropic (Claude)',
        protocol=PROTOCOL_ANTHROPIC,
        base_url='https://api.anthropic.com',
        default_model='claude-sonnet-4-5',
        api_key_env='ANTHROPIC_API_KEY',
        json_mode=False,
    ),
    'custom': Provider(
        key='custom',
        label='Custom (OpenAI-compatible)',
        protocol=PROTOCOL_OPENAI,
        base_url='',
        default_model='',
        api_key_env='REVIEW_AI_CUSTOM_API_KEY',
    ),
}

#: The provider used when nothing is configured.
DEFAULT_PROVIDER = 'deepseek'


def get_provider(key: str | None) -> Provider:
    """Return a provider by key.

    Unknown or empty keys fall back to :py:data:`DEFAULT_PROVIDER`.
    """
    if key:
        provider = PROVIDERS.get(str(key).strip().lower())

        if provider is not None:
            return provider

    return PROVIDERS[DEFAULT_PROVIDER]


def get_provider_choices():
    """Return ``(key, label)`` tuples for a Django ChoiceField."""
    return [
        (key, provider.label)
        for key, provider in PROVIDERS.items()
    ]


def resolve_api_key(
    provider: Provider,
    option_value: str | None = None,
) -> str:
    """Return the API key for a provider.

    Lookup order (first match wins): ``REVIEW_AI_API_KEY``, then the
    provider-specific variable (e.g. ``DEEPSEEK_API_KEY``), then the tool
    setting. Environment variables win so that keys need not be stored in the
    database.
    """
    for env_name in (GENERIC_API_KEY_ENV, provider.api_key_env):
        if not env_name:
            continue

        value = os.environ.get(env_name)

        if value and value.strip():
            return value.strip()

    return (option_value or '').strip()


def resolve_base_url(
    provider: Provider,
    option_value: str | None = None,
) -> str:
    """Return the API endpoint for a provider.

    May be empty for a misconfigured custom provider.
    """
    for value in (option_value,
                  os.environ.get(BASE_URL_ENV),
                  provider.base_url):
        if value and value.strip():
            return value.strip()

    return ''


def resolve_model(
    provider: Provider,
    option_value: str | None = None,
) -> str:
    """Return the model name for a provider.

    May be empty if nothing is configured.
    """
    for value in (option_value,
                  os.environ.get(MODEL_ENV),
                  provider.default_model):
        if value and value.strip():
            return value.strip()

    return ''
