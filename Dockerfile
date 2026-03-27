FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Render injects PORT env var (default 8000)
ENV PORT=8000
ENV HOST=0.0.0.0

EXPOSE ${PORT}

# Run the MCP server via uvicorn
CMD uvicorn server:app --host ${HOST} --port ${PORT}
