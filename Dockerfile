FROM python:3.9-slim
COPY script.py /script.py
ENTRYPOINT ["python", "/script.py"]