# Admin UI. Based on the docker CLI image so it can rebuild and restart the
# bot through the host's Docker socket (mounted by docker-compose.yml).
FROM docker:28-cli

RUN apk add --no-cache python3 py3-pip git bash \
 && git config --global --add safe.directory '*'

WORKDIR /app
COPY admin/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY admin/ .
RUN chmod +x update.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python3", "app.py"]
