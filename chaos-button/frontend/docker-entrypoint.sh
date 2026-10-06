#!/bin/sh
# Renders the nginx server block from the template using only the
# variables we explicitly list, so nginx's own $host/$remote_addr/etc.
# are left untouched by envsubst.
set -eu

: "${BACKEND_HOST:=backend}"
: "${BACKEND_PORT:=8080}"
: "${LISTEN_PORT:=8080}"
export BACKEND_HOST BACKEND_PORT LISTEN_PORT

envsubst '${BACKEND_HOST} ${BACKEND_PORT} ${LISTEN_PORT}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
