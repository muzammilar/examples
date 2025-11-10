# Add python image and install uv
FROM python:3.12
# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container
ADD . /app

# See https://docs.astral.sh/uv/guides/integration/docker/#available-images
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# sync with uv
RUN uv sync
