# InSync webapp — сборка Preact + Vite и раздача статики через nginx.

# --- Этап сборки ---
FROM node:22-alpine AS build
WORKDIR /app

# Зависимости отдельным слоём (кеш при неизменном package-lock.json).
COPY package.json package-lock.json ./
RUN npm ci

# Исходники и production-сборка (tsc + vite build -> /app/dist).
COPY . .
RUN npm run build

# --- Этап раздачи ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
