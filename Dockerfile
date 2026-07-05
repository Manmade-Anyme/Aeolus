FROM python:3.11-slim

WORKDIR /app

# Copy metadata and source before install so the package map includes aeolus
# (editable install without src/ present produced no module mapping)
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Top-level config package (weight/threshold tables) imported at runtime;
# resolved via /app on sys.path when main.py runs from WORKDIR
COPY config/ config/
COPY main.py .

# Run scheduler (exits after one session, then Fly scales to 0)
CMD ["python", "main.py"]
