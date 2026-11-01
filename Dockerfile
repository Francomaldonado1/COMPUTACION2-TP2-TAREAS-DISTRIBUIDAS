FROM python:3.12-slim

# Logs sin buffer: se ven en tiempo real con 'docker compose logs'
ENV PYTHONUNBUFFERED=1

WORKDIR /srv

# Primero las dependencias: así Docker cachea esta capa si el código cambia
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

EXPOSE 8000

# Forma 'exec' (lista): uvicorn es el PID 1 y recibe SIGTERM directo de 'docker stop'
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
