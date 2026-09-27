# Use an official Python runtime as a parent image
FROM python:3.10

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install testing requirements
RUN pip install --no-cache-dir -r test_requirements.txt

# Make port 80 available to the world outside this container
EXPOSE 80

# Define environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run the application by default
CMD ["python", "app.py"]
