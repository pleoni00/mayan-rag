ARG PYTHON_BASE_IMAGE=python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893
FROM ${PYTHON_BASE_IMAGE}
COPY requirements.embedding.txt /tmp/requirements.embedding.txt
RUN pip install --timeout 120 --retries 5 --no-cache-dir -r /tmp/requirements.embedding.txt
COPY external /app
WORKDIR /app
CMD ["python", "worker.py"]
