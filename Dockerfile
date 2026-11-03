FROM python:3.11-slim-bookworm

# keep in sync with the uv version in .github/workflows/main.workflow.yml
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

# Run as a non-root user whose uid matches a typical host user, so files
# written into the mounted /project are not owned by root on the host.
ARG USER_UID=1000
RUN useradd --create-home --uid $USER_UID cft

# The environment lives outside /project because the Makefile and
# devcontainer bind-mount the repository over /project at runtime.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

RUN mkdir /opt/venv /project && chown cft /opt/venv /project
USER cft
WORKDIR /project

# install dependencies first so this layer is cached until uv.lock changes
COPY --chown=cft pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-install-project

# install the project itself (editable, so the mounted src/ is what runs)
COPY --chown=cft src ./src
RUN uv sync --locked

CMD ["/bin/bash"]
