FROM python:3.11-slim
WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server /app/server
RUN useradd -u 10001 -m farm && mkdir -p /app/server/data && chown -R farm:farm /app/server/data
USER farm
EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
