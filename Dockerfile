FROM python:3.8.0-alpine

COPY poetry.lock pyproject.toml ./

RUN apk add --no-cache gcc libffi-dev musl-dev openssl-dev && \
    pip install --upgrade pip && \
    pip --no-cache-dir install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev && \
    pip uninstall poetry -y && \
    rm -rf ~/.config/pypoetry

VOLUME /data

COPY athop_transaction_scraper.py /app/athop_transaction_scraper.py

CMD python /app/athop_transaction_scraper.py
