FROM node:20-alpine AS builder
WORKDIR /app

# Install ImageMagick for icon generation
RUN apk add --no-cache imagemagick

COPY package*.json ./
RUN npm ci
COPY . .

# Generate PNG icons from SVG source
RUN magick -background '#0f1117' -fill '#6c63ff' public/icons/icon.svg \
      -resize 192x192 public/icons/icon-192.png && \
    magick -background '#0f1117' -fill '#6c63ff' public/icons/icon.svg \
      -resize 512x512 public/icons/icon-512.png

RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# Minimal config: serve SPA, let host nginx handle routing and proxying
RUN printf 'events{}\nhttp{\n  include mime.types;\n  server{\n    listen 80;\n    root /usr/share/nginx/html;\n    location / { try_files $uri /index.html; }\n  }\n}' \
      > /etc/nginx/nginx.conf
EXPOSE 80
