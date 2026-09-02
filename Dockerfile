# ---- build ----
FROM node:20-alpine AS build
WORKDIR /app
# Корпоративный SSL-inspection прокси (Unitel/Beeline FortiGate) переподписывает
# весь внешний HTTPS своим корнем — без него `npm ci` падает на каждом тарболе с
# SELF_SIGNED_CERT_IN_CHAIN. NODE_EXTRA_CA_CERTS ДОБАВЛЯЕТ корень к встроенному
# бандлу Node (не заменяет его), поэтому образ собирается и вне корпсети.
COPY docker/unitel-root-ca.crt /usr/local/share/ca-certificates/unitel-root-ca.crt
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/unitel-root-ca.crt
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- serve ----
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# The nginx image's entrypoint renders /etc/nginx/templates/*.template through
# envsubst into /etc/nginx/conf.d/. The FILTER keeps it to BACKEND_UPSTREAM so
# nginx's own $host / $scheme / $uri variables are left alone.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_UPSTREAM=http://app:8000 \
    NGINX_ENVSUBST_FILTER=BACKEND_UPSTREAM
EXPOSE 80
