# Base image with PyTorch 2.7.0 + CUDA 12.8 (supports RTX 5070 sm_120)
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Set working directory inside the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    g++ \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    wget \
    gnupg2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and core build tools
RUN pip install --upgrade pip setuptools wheel

# Copy the codebase into the container
COPY . .

# Install other dependencies from requirements.txt
# Note: PyTorch, torchvision, and torchaudio are already included in base image
RUN pip install -r requirements.txt

# Install CuPy for CUDA 12.x
RUN pip uninstall -y cupy-cuda11x cupy-cuda12x cupy || true && \
    pip install cupy-cuda12x

# Default: show the command list. Override with e.g.
#   docker compose run pipeline python cli.py segment nike
CMD ["python", "cli.py", "list"]
