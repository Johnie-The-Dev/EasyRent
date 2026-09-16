# Use a small official Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1

# Set the working directory in the container
WORKDIR /app

# Copy only requirements first for caching
COPY requirements.txt /app/

# Install dependencies
RUN python -m pip install --upgrade pip \
	&& python -m pip install -r requirements.txt

# Copy the rest of the project
COPY . /app/

# Expose the port Django runs on
EXPOSE 8000

# Run Django server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
