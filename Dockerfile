FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY pyproject.toml .
COPY src src/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Create entrypoint
COPY main.py .

# Run the scheduler (exits after one session)
CMD ["python", "main.py"]
