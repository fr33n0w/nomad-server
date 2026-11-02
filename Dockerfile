FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nomad_server.py ./
COPY nserver ./nserver
COPY docker/entrypoint.py ./docker/

# server.cfg, nodes (identities!), counters, logs and the Reticulum config live here
VOLUME /data
EXPOSE 8484
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
