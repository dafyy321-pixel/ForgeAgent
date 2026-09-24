FROM python:3.12.10-slim
RUN groupadd -g 10001 agent && useradd -u 10001 -g agent -m agent
USER 10001:10001
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "--version"]
