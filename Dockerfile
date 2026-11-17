FROM python:3.9-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY wind_data_converter.py diagnose.py healthcheck.py ./

# Data, logs and config are mounted from the host at run time
RUN mkdir -p data logs config

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=America/Anchorage

# Healthy = the collector loop is still rewriting the station status files
HEALTHCHECK --interval=2m --timeout=10s --start-period=1m --retries=2 \
  CMD ["python", "healthcheck.py"]

# Run the script
CMD ["python", "wind_data_converter.py"]
