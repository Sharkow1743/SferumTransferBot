FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy project files
COPY data_handler.py logger.py main.py ./

ENV IS_DOCKER=True

# Command to run your application
CMD ["python", "main.py"]