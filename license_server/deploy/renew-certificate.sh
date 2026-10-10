#!/bin/sh
set -eu
license_certificate=/opt/dy-license/license_server/runtime/certificates/live/license.txblog.cn/fullchain.pem
license_before=$(sha256sum "$license_certificate")
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /tmp --tmpfs /var/lib/letsencrypt --tmpfs /var/log/letsencrypt \
    -v /opt/dy-license/license_server/runtime/certificates:/etc/letsencrypt \
    -v /opt/dy-license/license_server/runtime/acme:/var/www/license-acme \
    certbot/certbot@sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20 \
    renew --webroot -w /var/www/license-acme --quiet "$@"
license_after=$(sha256sum "$license_certificate")
if [ "$license_before" != "$license_after" ]; then
    docker exec blog-frontend nginx -t
    docker exec blog-frontend nginx -s reload
fi
