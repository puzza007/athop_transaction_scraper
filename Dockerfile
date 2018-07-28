FROM python:3.7.0-alpine

RUN pip install requests && \
    pip install beautifulsoup4

VOLUME /data

COPY athop_transaction_scraper.py /app/athop_transaction_scraper.py

CMD python /app/athop_transaction_scraper.py
