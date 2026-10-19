FROM python:3.12-slim
WORKDIR /app
COPY envs/wildfire_response/core /app/core
COPY envs/wildfire_response/openenv /app/openenv
RUN pip install --no-cache-dir /app/core /app/openenv
EXPOSE 7860
CMD ["python", "-m", "wildfire_openenv.server", "--host", "0.0.0.0", "--port", "7860"]
