FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /srv
COPY traffic.py .
USER 10001
CMD ["python", "traffic.py"]
