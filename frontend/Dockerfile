# 多阶段：node 构建静态产物 → nginx 托管 SPA。
# edge-nginx 把 dev.substantia.ai 的 / 反代到本容器，/api/ 反代到 backend（同源，cookie 正常）。
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx-spa.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
