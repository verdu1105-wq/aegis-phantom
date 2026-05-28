FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TIKTOK_ACCESS_TOKEN=act.8Y9bcANNjK5ONacA0V6nhWFGdekMauVveOFfPdsoAozAzQ1qrmnn2QiXiCy5!6425.u1
EXPOSE 8080
CMD ["python", "main.py"]


