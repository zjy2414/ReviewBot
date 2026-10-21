"""Review AI: multi-provider AI code review for Review Bot.

The tool is not tied to a vendor: it talks to DeepSeek, OpenAI, Qwen,
Moonshot, Zhipu, Anthropic, local Ollama models, or any OpenAI-compatible
gateway.

Keep this module free of Review Bot imports: ``setup.py`` reads it at build
time, before dependencies are installed.
"""

#: The version of this package.
__version__ = '1.0.0'

#: The tool ID used for the Review Bot entry point and the Celery queue.
TOOL_ID = 'reviewai'
