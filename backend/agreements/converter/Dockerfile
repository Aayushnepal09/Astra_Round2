FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY worker.py /opt/converter.py
ENV HOME=/tmp/home LANG=C.UTF-8
USER 65534:65534
ENTRYPOINT ["python", "-I", "/opt/converter.py"]
