# Build Stage
FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production Stage: nginx as a non-root user, listening on 8080
FROM nginxinc/nginx-unprivileged:alpine

# Copy the nginx config template; rendered at start with only MAX_UPLOAD_MB substituted
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
ENV NGINX_ENVSUBST_FILTER=^MAX_UPLOAD_MB$
ENV MAX_UPLOAD_MB=100

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# The base image already runs as nginx (uid 101); stated explicitly for scanners and readers.
USER 101

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
