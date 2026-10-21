#!/bin/sh
#
# Start the Review Bot worker, consuming the Review AI tool queue
# ("reviewai.<tool version>").
#
# Required environment variables:
#
#     BROKER_URL         The AMQP (or Redis) URL of the message broker.
#
# Optional environment variables:
#
#     REVIEW_AI_API_KEY  The API key for any provider (recommended).
#                        Provider-specific variables such as DEEPSEEK_API_KEY,
#                        OPENAI_API_KEY, DASHSCOPE_API_KEY, MOONSHOT_API_KEY,
#                        ZHIPUAI_API_KEY, and ANTHROPIC_API_KEY also work.
#     REVIEW_AI_BASE_URL Overrides the endpoint for every provider.
#     REVIEW_AI_MODEL    Overrides the model for every provider.
#     LOG_LEVEL          The log level (default: INFO).
#     NUM_WORKERS        The Celery concurrency (default: Celery's own default).
#     REVIEWBOT_CONFIG_FILE
#                        Path to the optional Python config file.

set -e

if [ -z "${BROKER_URL}" ]; then
    echo 'error: BROKER_URL is not set' >&2
    exit 1
fi

LOG_LEVEL="${LOG_LEVEL:-INFO}"

if [ -n "${NUM_WORKERS}" ]; then
    exec reviewbot -b "${BROKER_URL}" -l "${LOG_LEVEL}" -c "${NUM_WORKERS}"
fi

exec reviewbot -b "${BROKER_URL}" -l "${LOG_LEVEL}"
