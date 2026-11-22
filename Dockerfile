# Static build of the Railsight app: the scan results are plain JSON/GeoJSON/PNG/JPEG files in app/public,
# so the container serves files only (no model calls, no API keys). Base map and aerial tiles load from geo.admin.ch.
FROM node:22-alpine AS build
WORKDIR /app
COPY app/package.json app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
