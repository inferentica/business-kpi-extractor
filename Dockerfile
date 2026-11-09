# The extractor as a worker image: the same code and tests the GitHub runners use, for x64 or ARM (Oracle's Ampere).
# It holds no secrets; a worker is given its control-plane credential when it starts.
FROM python:3.12-slim-bookworm

# OpenCV (for chart OCR) needs libGL and GLib; GNU time reports a run's peak memory.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 time \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SEC_USER_AGENT="TradeTracker admin@inferentica.com"
WORKDIR /app
COPY pyproject.toml ./
COPY kpi_extractor ./kpi_extractor
RUN pip install '.[test]'
COPY tests ./tests
COPY evals ./evals

RUN useradd --create-home worker && chown -R worker /app
USER worker
# The tests gate the image, as they gate every extraction on the runners.
RUN pytest -q -x -p no:cacheprovider
CMD ["python", "-m", "kpi_extractor"]
