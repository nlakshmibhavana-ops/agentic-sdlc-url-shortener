# Runs the orchestrator and all three scenarios in a clean container.
#   docker build -t agentflow .
#   docker run --rm agentflow                         # full non-interactive demo
#   docker run --rm -it agentflow run greenfield      # interactive run (prompts for approvals)
#   docker run --rm -it -p 8000:8000 agentflow sh -c \
#     "python -m agentflow run greenfield --run-id gf && python -m agentflow serve gf --host 0.0.0.0"
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PY=python
WORKDIR /app

COPY pyproject.toml README.md ./
COPY agentflow ./agentflow
# Editable install: the CLI locates scenarios/ and fixtures/ relative to the source tree.
RUN pip install -e .

COPY fixtures ./fixtures
COPY scenarios ./scenarios
COPY scripts ./scripts
COPY tests ./tests

RUN useradd --create-home agent && chown -R agent /app
USER agent

EXPOSE 8000
ENTRYPOINT ["python", "-m", "agentflow"]
CMD ["demo"]
