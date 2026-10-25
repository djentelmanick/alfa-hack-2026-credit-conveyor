FROM node:26.12.1-alpine3.24 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.js ./
COPY src ./src
# API и WebSocket идут через тот же origin, nginx проксирует /api на бэкенд.
ENV VITE_API_URL=/api/v1 \
    VITE_WS_URL=
RUN npm run build

FROM nginx:1.30.6-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
