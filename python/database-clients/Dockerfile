# Use an official Python runtime as a parent image
FROM python:3.10-alpine

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container
COPY . /app

# Install any needed packages specified in requirements.txt
#RUN pip install --no-cache-dir -r requirements.txt
RUN pip install -r requirements.txt

# Install testing requirements
#RUN pip install --no-cache-dir -r test_requirements.txt
RUN pip install -r test_requirements.txt

# Install modules
RUN apk add --no-cache git make bash

# Define environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run the application by default
CMD ["make", "test"]
