# Custom Caddy: adds the replace-response module so we can inject
# Senebiclabs branding (title, favicon, logo) into Label Studio's HTML.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddyserver/replace-response

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
