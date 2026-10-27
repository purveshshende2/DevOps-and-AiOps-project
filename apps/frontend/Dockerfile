FROM python:3.13-slim

WORKDIR /app
COPY server.py requirements.txt ./
COPY static ./static

EXPOSE 8080
CMD ["python", "server.py"]
