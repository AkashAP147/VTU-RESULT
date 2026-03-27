FROM python:3.11-slim

# Install Tesseract OCR (Optional fallback for Captcha solver)
RUN apt-get update && apt-get install -y tesseract-ocr && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app code
COPY . .

# Expose port (Render/Heroku/Railway pass PORT env)
EXPOSE 5000

# Start app using gunicorn (Production wsgi)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "server:app"]
