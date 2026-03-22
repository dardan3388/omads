FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMADS_OPEN_BROWSER=0

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["omads", "gui", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
