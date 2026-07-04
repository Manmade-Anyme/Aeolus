FROM python:3.11-slim

WORKDIR /app

# Copy only what's needed for pip install
COPY pyproject.toml .

# Install dependencies (cache layer)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copy source code
COPY src/ src/
COPY main.py .

# Run scheduler (exits after one session, then Fly scales to 0)
CMD ["python", "main.py"]
