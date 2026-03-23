FROM python:3.12-slim

WORKDIR /app

# Copy adapter files
COPY requirements.txt .
COPY adapter.py .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD ["python", "adapter.py"]
