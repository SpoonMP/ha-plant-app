# Home Assistant base image with Python (Alpine). Multi-arch: aarch64 (Pi) and amd64.
FROM ghcr.io/home-assistant/base-python:3.13-alpine3.24

WORKDIR /app

# Starlette, uvicorn, python-multipart and paho-mqtt are pure Python; Pillow has a
# ready-made build for the Pi (musllinux aarch64), so nothing is compiled on install.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app /app/plants

ENV PYTHONUNBUFFERED=1 \
    PLANTS_DATA_DIR=/data

# The base image's own init (S6 overlay) runs this as the main program.
CMD ["python3", "-m", "uvicorn", "plants.main:app", "--host", "0.0.0.0", "--port", "8099", "--no-access-log"]
