# Мини-приложение для стенда с HTTPS: сборка Vite и раздача статики Caddy на :8000.
# Контекст — корень репозитория: типы и моки берут файлы из contracts/.
# Внешний Caddy (deploy/Caddyfile) отдаёт /api/v1/* в api, поэтому фронт ходит на /api.
FROM node:22.12.0-alpine AS build
WORKDIR /repo/max_app
COPY max_app/package.json max_app/package-lock.json max_app/.npmrc ./
RUN npm ci
COPY contracts /repo/contracts
COPY max_app/ ./
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM caddy:2.10.0-alpine
COPY --from=build /repo/max_app/dist /srv
EXPOSE 8000
CMD ["caddy", "file-server", "--root", "/srv", "--listen", ":8000"]
