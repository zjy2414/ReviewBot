ARG PYTHON_BASE_IMAGE=python:3.12-slim

FROM ${PYTHON_BASE_IMAGE}

ARG REVIEWBOT_VERSION=
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    BROKER_URL=amqp://reviewbot:reviewbot123@rabbitmq:5672/reviewbot \
    LOG_LEVEL=INFO

# API keys are intentionally NOT baked into the image. Pass one at runtime,
# either REVIEW_AI_API_KEY (works for every provider) or a provider-specific
# variable such as DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY.

# gosu is used by the entrypoint to drop privileges; procps provides pgrep
# for the health check. ca-certificates is required for HTTPS calls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gosu \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/review-ai

# Install the worker and then this tool. The tool registers itself through
# the "reviewbot.tools" entry point, so no further wiring is needed.
COPY . /tmp/review-ai
# Review Bot 4.0 imports pkg_resources, which setuptools 81+ no longer ships.
RUN pip install \
        --root-user-action=ignore \
        "setuptools<81" \
        "reviewbot-worker${REVIEWBOT_VERSION:+==${REVIEWBOT_VERSION}}" \
        /tmp/review-ai \
    && rm -rf /tmp/review-ai \
    && python -c "import review_ai; print('review-ai', review_ai.__version__)"

# Match the UID used by the official Review Bot images.
RUN useradd --uid 1001 --create-home --shell /bin/bash reviewbot \
    && mkdir -p /config /repos \
    && chown -R reviewbot:reviewbot /opt/review-ai /config /repos

COPY docker/ /opt/review-ai/docker/
RUN chmod 755 /opt/review-ai/docker/entrypoint.sh \
             /opt/review-ai/docker/run-worker.sh

VOLUME ["/config", "/repos"]

# Verifies that a worker process is alive. This deliberately does not talk to
# the broker, so a temporarily unreachable RabbitMQ does not mark the
# container unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD pgrep -f "reviewbot -b" > /dev/null || exit 1

ENTRYPOINT ["/opt/review-ai/docker/entrypoint.sh"]
CMD ["/opt/review-ai/docker/run-worker.sh"]
