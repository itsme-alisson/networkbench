FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NETWORKBENCH_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home networkbench
COPY requirements-collections.yml .
RUN ansible-galaxy collection install -r requirements-collections.yml -p /app/.collections
COPY app ./app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]

# Run the same API/example suite inside the built Linux environment.
FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
COPY examples ./examples
USER 10001:10001
ENV NETWORKBENCH_DATA_DIR=/tmp/networkbench-test
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM runtime AS final
