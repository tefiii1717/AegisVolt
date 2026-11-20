# syntax=docker/dockerfile:1.7
FROM prefecthq/prefect:3-python3.12
WORKDIR /opt/flows
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY audit.py flows.py ./
RUN useradd --uid 10001 --no-create-home flows
USER 10001
ENV PREFECT_RESULTS_PERSIST_BY_DEFAULT=false \
    PREFECT_HOME=/tmp/prefect
CMD ["python", "flows.py"]
