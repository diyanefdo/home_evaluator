FROM python:3.12-slim

WORKDIR /app

# Install deps first so this layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY evaluator/ ./evaluator/
COPY webapp.py .
COPY static/ ./static/

# matplotlib uses the Agg backend (set in charts.py) — no display needed.
# Give it a writable, container-local config dir.
ENV MPLCONFIGDIR=/tmp/mpl

EXPOSE 8000
CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8000"]
