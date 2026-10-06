# NOTE: ubuntu:26.04 ("Resolute") is a future Ubuntu release. If this tag is
# not yet published to Docker Hub at build time, swap it for ubuntu:24.04
# (the latest LTS) without any other changes required.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ca-certificates \
        python3-pydantic-core \
        python3-fastapi \
        python3-requests \
        python3-requests-oauthlib \
        python3-kubernetes \
        python3-uvicorn \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .

RUN useradd --create-home --uid 1001 chaosuser
USER chaosuser

EXPOSE 8080

CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
