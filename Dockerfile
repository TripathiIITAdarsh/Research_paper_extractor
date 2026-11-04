FROM python:3.12-slim

# The host (Render, Fly, etc.) sets PORT at runtime via env var.
# Fallback to 7860 if nothing is provided.
ENV HOST=0.0.0.0
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

EXPOSE 7860

# Production entrypoint — waitress WSGI, debug off
CMD ["python", "app.py"]
