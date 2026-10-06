# NOTE: ubuntu:26.04 ("Resolute") is a future Ubuntu release. If this tag is
# not yet published to Docker Hub at build time, swap it for ubuntu:24.04
# (the latest LTS) without any other changes required.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Disable the distro's default site (it listens on port 80 as
# `default_server`, which would fail to bind once we drop to a
# non-root user and also clashes with our own templated server block).
RUN rm -f /etc/nginx/sites-enabled/default

# Let the non-root nginx user own everything nginx needs to write to
# (temp dirs for client bodies/proxying, cache, logs, pid file, and the
# conf.d dir our entrypoint renders into).
RUN mkdir -p /var/cache/nginx /var/log/nginx /var/lib/nginx \
    && chown -R www-data:www-data /var/cache/nginx /var/log/nginx /var/lib/nginx /etc/nginx \
    && touch /var/run/nginx.pid \
    && chown www-data:www-data /var/run/nginx.pid

WORKDIR /usr/share/nginx/html
COPY index.html app.js style.css ./

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Where to reach the backend's ClusterIP Service, and what port nginx
# itself should listen on. Overridden by the Helm chart to point at the
# backend Service DNS name within the release namespace.
ENV BACKEND_HOST=backend \
    BACKEND_PORT=8080 \
    LISTEN_PORT=8080

EXPOSE 8080

USER www-data

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
