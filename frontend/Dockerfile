# Build Stage
FROM node:24-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production Stage: nginx as a non-root user, listening on 8080. At start it writes only /tmp (pid, temp
# files) and /etc/nginx/conf.d (rendered templates): with a read-only root filesystem, mount both writable.
FROM nginx/nginx-unprivileged:alpine

# Copy the nginx config template; rendered at start with only these variables substituted.
# BACKEND_UPSTREAM: where the API lives (compose/k8s service name, or http://localhost:8000
# when both containers share a Fargate task / pod).
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
# Rendered to conf.d/security-headers.inc (not auto-loaded: only *.conf is); included by each location.
COPY nginx/security-headers.inc.template /etc/nginx/templates/security-headers.inc.template
# http-context limit_req zones, rendered to conf.d/limits.conf.
COPY nginx/limits.conf.template /etc/nginx/templates/limits.conf.template
# REAL_IP_FROM (CIDRs of the proxy in front of nginx) -> conf.d/real-ip.conf. Empty = trust no proxy.
COPY --chmod=0755 nginx/25-real-ip.sh /docker-entrypoint.d/25-real-ip.sh
ENV REAL_IP_FROM=""
ENV NGINX_ENVSUBST_FILTER=^(MAX_UPLOAD_MB|BACKEND_UPSTREAM|HSTS_HEADER)$
ENV HSTS_HEADER=""
ENV MAX_UPLOAD_MB=100
ENV BACKEND_UPSTREAM=http://backend:8000

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# The base image already runs as nginx (uid 101); stated explicitly for scanners and readers.
USER 101

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
