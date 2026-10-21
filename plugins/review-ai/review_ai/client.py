"""Chat clients for the model providers supported by Review AI.

Requests are sent through LangChain chat models:

* OpenAI-compatible gateways (DeepSeek, OpenAI, Qwen, Moonshot, Zhipu, Ollama,
  custom gateways) use :class:`~langchain_openai.ChatOpenAI`.
* The Anthropic Messages API uses
  :class:`~langchain_anthropic.ChatAnthropic`.

Each request runs as an LCEL chain::

    ChatPromptTemplate -> chat model -> response normalizer

The normalizer keeps a useful error message for reasoning models that spend
the whole output budget on reasoning tokens instead of an answer.
"""

from __future__ import annotations

import json
import logging

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from review_ai.providers import PROTOCOL_ANTHROPIC, Provider


#: The default number of seconds to wait for a response.
DEFAULT_TIMEOUT = 500.0

#: The default number of retries for transient failures.
DEFAULT_MAX_RETRIES = 2

#: Finish reasons that mean the answer was cut off by the token budget.
_TRUNCATED_FINISH_REASONS = frozenset(['length', 'max_tokens'])

#: Error substrings that mean "this gateway rejects response_format".
_JSON_MODE_HINTS = ('response_format', 'json_object', 'json mode')

#: Placeholder key for providers that accept any value (Ollama).
_PLACEHOLDER_API_KEY = 'not-needed'


class ChatError(Exception):
    """An error communicating with a model provider."""

    def __init__(
        self,
        message: str,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)

        self.status = status
        self.body = body


class BaseChatClient:
    """Base class for chat clients."""

    #: The provider this client talks to.
    provider: Provider

    #: Whether this client can request a JSON object response.
    supports_json_mode: bool = True

    def __init__(
        self,
        provider: Provider,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the client.

        Raises:
            ChatError:
                The provider is misconfigured.
        """
        if provider.requires_api_key and not api_key:
            raise ChatError(
                'A %s API key is required. Set REVIEW_AI_API_KEY (or %s) on '
                'the worker, or fill in the API key tool option.'
                % (provider.label,
                   provider.api_key_env or 'the provider-specific variable'))

        if not base_url:
            raise ChatError(
                'No API endpoint is configured for %s. Set the base URL tool '
                'option, or the REVIEW_AI_BASE_URL environment variable.'
                % provider.label)

        if not model:
            raise ChatError(
                'No model is configured for %s. Set the model tool option, or '
                'the REVIEW_AI_MODEL environment variable.'
                % provider.label)

        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.logger = logger or logging.getLogger(__name__)

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        json_mode: bool | None = None,
    ) -> str:
        """Send a chat request and return the assistant's message.

        ``json_mode`` defaults to the client's capability.

        Raises:
            ChatError:
                The request failed, or the response was unusable.
        """
        raise NotImplementedError


class LangChainChatClient(BaseChatClient):
    """A chat client backed by a LangChain chat model."""

    def __init__(
        self,
        provider: Provider,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        logger: logging.Logger | None = None,
        chat_model: Runnable | None = None,
    ) -> None:
        """Initialize the client.

        ``chat_model`` is only meant for tests; production code builds the
        model from the provider configuration.
        """
        super().__init__(provider=provider,
                         api_key=api_key,
                         base_url=base_url,
                         model=model,
                         timeout=timeout,
                         max_retries=max_retries,
                         logger=logger)

        # Anthropic has no JSON mode; it relies on the prompt alone.
        self.supports_json_mode = provider.json_mode

        self._chat_model = (chat_model
                            if chat_model is not None
                            else self._build_chat_model())

    ######################################################################
    # Model configuration
    ######################################################################

    def _build_chat_model(self) -> Runnable:
        """Return the LangChain chat model for this provider.

        Raises:
            ChatError:
                The LangChain integration for this protocol is unavailable.
        """
        try:
            if self.provider.protocol == PROTOCOL_ANTHROPIC:
                from langchain_anthropic import ChatAnthropic

                return ChatAnthropic(
                    model=self.model,
                    anthropic_api_key=self.api_key or _PLACEHOLDER_API_KEY,
                    anthropic_api_url=self.base_url,
                    default_request_timeout=self.timeout,
                    max_retries=self.max_retries,
                )

            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=self.model,
                openai_api_key=self.api_key or _PLACEHOLDER_API_KEY,
                openai_api_base=self.base_url,
                request_timeout=self.timeout,
                max_retries=self.max_retries,
            )
        except ImportError as e:
            raise ChatError(
                'The LangChain integration for %s is not installed (%s). '
                'Install langchain-openai and langchain-anthropic, then '
                'restart the worker.'
                % (self.provider.label, e)) from e

    def _build_chain(
        self,
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> Runnable:
        """Return the LCEL chain used for one request."""
        prompt = ChatPromptTemplate.from_messages([
            ('system', '{system}'),
            ('human', '{user}'),
        ])

        model = self._chat_model.bind(
            max_tokens=max_tokens,
            temperature=temperature,
            **_json_mode_kwargs(self.provider, json_mode))

        normalizer = RunnableLambda(
            lambda message: self._normalize_response(message,
                                                    max_tokens=max_tokens))

        return prompt | model | normalizer

    ######################################################################
    # Request handling
    ######################################################################

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        json_mode: bool | None = None,
    ) -> str:
        """Send a chat request and return the assistant's message.

        ``json_mode`` defaults to the client's capability.

        Raises:
            ChatError:
                The request failed, or the response was unusable.
        """
        if json_mode is None:
            json_mode = self.supports_json_mode

        try:
            return self._invoke(system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                json_mode=json_mode)
        except ChatError:
            raise
        except Exception as e:
            error = _translate_error(e, self.provider)

            if not self._should_retry_without_json_mode(json_mode, error):
                raise error from None

            self.logger.warning(
                '%s rejected JSON mode (%s); retrying without it.',
                self.provider.label, error)

            try:
                return self._invoke(system_prompt=system_prompt,
                                    user_prompt=user_prompt,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    json_mode=False)
            except ChatError:
                raise
            except Exception as retry_error:
                raise _translate_error(retry_error,
                                       self.provider) from None

    def _invoke(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Run the chain once and return the answer text."""
        chain = self._build_chain(max_tokens=max_tokens,
                                  temperature=temperature,
                                  json_mode=json_mode)

        return chain.invoke({
            'system': system_prompt,
            'user': user_prompt,
        })

    def _normalize_response(self, message: Any, *, max_tokens: int) -> str:
        """Return the answer text, or raise a helpful :py:class:`ChatError`.

        Raises:
            ChatError:
                The response was empty or truncated by the token budget.
        """
        text = _message_text(message)
        metadata = getattr(message, 'response_metadata', None) or {}
        finish_reason = str(
            metadata.get('finish_reason') or
            metadata.get('stop_reason') or
            '').lower()

        usage = getattr(message, 'usage_metadata', None) or {}

        self.logger.debug('%s response received (model=%s, '
                          'input_tokens=%s, output_tokens=%s, '
                          'finish_reason=%s)',
                          self.provider.label,
                          metadata.get('model', self.model),
                          usage.get('input_tokens'),
                          usage.get('output_tokens'),
                          finish_reason or 'unknown')

        if not text.strip():
            if finish_reason in _TRUNCATED_FINISH_REASONS:
                raise ChatError(
                    '%s stopped at the max_tokens limit before producing an '
                    'answer. Reasoning tokens consume the same budget, so '
                    'raise the max output tokens option (currently %d) or '
                    'review fewer files per request.'
                    % (self.provider.label, max_tokens))

            raise ChatError(
                '%s returned an empty response. This usually means the change '
                'was too large for the model, or max_tokens was exhausted.'
                % self.provider.label)

        return text

    @staticmethod
    def _should_retry_without_json_mode(
        json_mode: bool,
        error: ChatError,
    ) -> bool:
        """Return whether the request should be retried without JSON mode.

        Only errors that actually mention the JSON mode parameter are retried.
        Blindly retrying every 400/422 used to hide context-length and quota
        errors behind a misleading "rejected JSON mode" message.
        """
        if not json_mode or error.status not in (400, 422):
            return False

        message = str(error).lower()

        if any(hint in message for hint in _JSON_MODE_HINTS):
            return True

        return error.status == 422


def create_chat_client(
    provider: Provider,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    logger: logging.Logger | None = None,
) -> BaseChatClient:
    """Create a chat client for a provider.

    Raises:
        ChatError:
            The provider or its configuration is invalid.
    """
    if provider.protocol not in ('openai', PROTOCOL_ANTHROPIC):
        raise ChatError('Unsupported provider protocol %r for %s.'
                        % (provider.protocol, provider.label))

    return LangChainChatClient(provider=provider,
                               api_key=api_key,
                               base_url=base_url,
                               model=model,
                               timeout=timeout,
                               max_retries=max_retries,
                               logger=logger)


def _json_mode_kwargs(provider: Provider, json_mode: bool) -> dict[str, Any]:
    """Return the request kwargs enabling JSON mode, if supported.

    Anthropic has no ``response_format`` equivalent, so it only gets the
    instructions already present in the prompt.
    """
    if not json_mode or provider.protocol == PROTOCOL_ANTHROPIC:
        return {}

    return {'response_format': {'type': 'json_object'}}


def _message_text(message: Any) -> str:
    """Return the text of an ``AIMessage``-like object."""
    content = getattr(message, 'content', message)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get('type') in ('text',
                                                                  'text_delta'):
                parts.append(str(block.get('text') or ''))

        return ''.join(parts)

    if content is None:
        return ''

    return str(content)


def _error_status(exc: Exception) -> int | None:
    """Return the HTTP status carried by a LangChain/SDK exception."""
    for attr in ('status_code', 'http_status', 'code'):
        value = getattr(exc, attr, None)

        if isinstance(value, int):
            return value

    response = getattr(exc, 'response', None)
    value = getattr(response, 'status_code', None)

    if isinstance(value, int):
        return value

    return None


def _error_body(exc: Exception) -> str:
    """Return the raw response body carried by an exception, if any."""
    body = getattr(exc, 'body', None)

    if isinstance(body, str):
        return body

    if isinstance(body, dict):
        return str(body)

    response = getattr(exc, 'response', None)
    text = getattr(response, 'text', None)

    if isinstance(text, str):
        return text

    return ''


def _translate_error(exc: Exception, provider: Provider) -> ChatError:
    """Convert a LangChain/SDK exception into a :py:class:`ChatError`."""
    status = _error_status(exc)
    detail = summarize_error(_error_body(exc) or str(exc))

    if status is not None:
        return ChatError('%s API returned HTTP %d: %s'
                         % (provider.label, status, detail),
                         status=status,
                         body=_error_body(exc)[:4096] or None)

    if isinstance(exc, TimeoutError) or 'timeout' in str(exc).lower():
        return ChatError('Timed out after waiting for %s: %s'
                         % (provider.label, detail))

    return ChatError('%s request failed (%s): %s'
                     % (provider.label, type(exc).__name__, detail))


def summarize_error(raw: str) -> str:
    """Return a short, single-line summary of an API response body."""
    if not raw:
        return '(empty response)'

    try:
        data = json.loads(raw)

        error = data.get('error')

        if isinstance(error, dict) and error.get('message'):
            return str(error['message'])[:500]
        elif isinstance(error, str):
            return error[:500]
    except (ValueError, AttributeError):
        pass

    return ' '.join(raw.split())[:500]
