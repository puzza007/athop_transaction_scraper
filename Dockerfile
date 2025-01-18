FROM python:3.8-slim-bullseye

COPY poetry.lock pyproject.toml ./

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev libssl-dev curl build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable && \
    export PATH="$HOME/.cargo/bin:$PATH" && \
    \
    pip install --upgrade pip && \
    pip --no-cache-dir install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev && \
    pip uninstall poetry -y && \
    \
    rm -rf /root/.cargo /root/.rustup && \
    rm -rf ~/.config/pypoetry

VOLUME /data

COPY athop_transaction_scraper.py /app/athop_transaction_scraper.py

CMD ["python", "/app/athop_transaction_scraper.py"]
