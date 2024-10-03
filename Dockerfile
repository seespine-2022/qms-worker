FROM python:3.9-slim

# Copy requirements file
COPY requirements.txt /requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r /requirements.txt

# Copy your script
COPY script.py /script.py

ENTRYPOINT ["python", "/script.py"]