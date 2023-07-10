FROM python:3.8.0

COPY poetry.lock pyproject.toml ./

RUN apt-get update && apt-get install -y gcc libffi-dev musl-dev libssl-dev curl && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    pip install --upgrade pip && \
    pip --no-cache-dir install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev && \
    pip uninstall poetry -y && \
    rm -rf ~/.config/pypoetry

VOLUME /data

COPY athop_transaction_scraper.py /app/athop_transaction_scraper.py

CMD python /app/athop_transaction_scraper.py
