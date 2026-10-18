FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 # Hidden Chromium for sites whose bot protection needs real JavaScript (Dell).
 # Installed as root so it lands in /opt/browsers, readable by the bot user.
 && playwright install --with-deps --no-shell chromium \
 && rm -rf /var/lib/apt/lists/* /root/.cache

COPY bot ./bot
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT
RUN useradd --create-home --uid 1000 bot
USER bot

CMD ["python", "-m", "bot.main"]
