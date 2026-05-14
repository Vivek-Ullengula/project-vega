FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir gradio requests

COPY gradio_app.py .

EXPOSE 7860

CMD ["python", "gradio_app.py"]
