FROM node:20-alpine AS builder

WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --frozen-lockfile

COPY frontend/ ./
RUN npm run build

# Production stage — Nginx to serve static files
FROM nginx:1.25-alpine
COPY --from=builder /app/frontend/dist /usr/share/nginx/html
COPY infrastructure/nginx/nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
