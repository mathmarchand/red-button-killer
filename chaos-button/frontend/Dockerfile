# NOTE: ubuntu:26.04 ("Resolute") is a future Ubuntu release. If this tag is
# not yet published to Docker Hub at build time, swap it for ubuntu:24.04
# (the latest LTS) without any other changes required.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY index.html app.js style.css ./

#RUN useradd --create-home --uid 1000 webuser
#USER webuser

EXPOSE 8080

CMD ["python3", "-m", "http.server", "8080"]
