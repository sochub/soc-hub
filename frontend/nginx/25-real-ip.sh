#!/bin/sh
# Renders conf.d/real-ip.conf from REAL_IP_FROM: the address(es) of the proxy in front of this nginx
# (k8s ingress controller, TLS proxy), comma- or space-separated CIDRs. nginx then takes the client
# address from X-Forwarded-For, walking right to left past those proxies (real_ip_recursive), so
# $remote_addr -- and the X-Real-IP the backend receives -- is the real client, not the proxy.
# Empty (default): $remote_addr stays the TCP peer, correct when nothing sits in front of nginx.
set -eu
out=/etc/nginx/conf.d/real-ip.conf
: > "$out"
[ -n "${REAL_IP_FROM:-}" ] || exit 0
for cidr in $(echo "$REAL_IP_FROM" | tr ',' ' '); do
    case "$cidr" in
        *[!0-9A-Fa-f:./]*) echo "$0: invalid REAL_IP_FROM entry: $cidr" >&2; exit 1 ;;
    esac
    echo "set_real_ip_from $cidr;" >> "$out"
done
printf 'real_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n' >> "$out"
