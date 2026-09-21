FROM nginx:alpine

# DS-0002: nginx master must start as root to bind :80 and load /etc/nginx.
# Worker processes drop to user `nginx` (image default nginx.conf). See
# security/trivy-exceptions.md.

# Remove default config
RUN rm /etc/nginx/nginx.conf

# Bake our production config into the image
# Config is version-controlled in git and rebuilt on every push via CI/CD
COPY nginx.prod.conf /etc/nginx/nginx.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
