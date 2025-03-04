FROM python:3.9-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create necessary directories
RUN mkdir -p data/status data/2025 data/daily/2025 logs

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=America/Anchorage

# Run the script
CMD ["python", "wind_data_converter.py"]
