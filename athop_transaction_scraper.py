import requests
from bs4 import BeautifulSoup
import re
import sqlite3
import os
import time

USERNAME = os.getenv('AT_USERNAME')
PASSWORD = os.getenv('AT_PASSWORD')
CARDS = [s.strip() for s in os.getenv('AT_CARDS').split(",")]
DATABASE_FILE = os.getenv('AT_DATABASE_FILE')
PERIOD = int(os.getenv('AT_PERIOD'))

s = requests.Session()

r = s.post('https://federation.aucklandtransport.govt.nz/adfs/ls/?wa=wsignin1.0&wtrealm=https://at.govt.nz&wctx=https://at.govt.nz&wreply=https://at.govt.nz/myat/',
           data={'UserName': USERNAME,
                 'Password': PASSWORD,
                 'AuthMethod': 'FormsAuthentication'})

soup = BeautifulSoup(r.text, 'html.parser')

inputs = soup.find_all('input')
input_map = {}
for i in inputs:
    try:
        input_map[i['name']] = i['value']
    except Exception:
        pass

action = soup.find('form')['action']

r2 = s.post(action, data=input_map)

fields = ['cardtransactionid', 'description', 'location', 'transactiondatetime', 'hop-balance-display', 'value', 'value-display', 'journey-id', 'refundrequested', 'refundable-value', 'transaction-type-description', 'transaction-type']


def ensure_database(DATABASE_FILE):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    res = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'").fetchone()
    if res is not None and res == ('transactions',):
        return
    schema = """
create table transactions (
       card_id,
       cardtransactionid,
       description,
       location,
       transactiondatetime,
       hop_balance_display,
       value,
       value_display,
       journey_id,
       refundrequested,
       refundable_value,
       transaction_type_description,
       transaction_type,
       PRIMARY KEY (card_id, cardtransactionid)
)
    """
    c.execute(schema)
    conn.commit()
    conn.close()


def scrape_transactions_for_card(conn, card_id):
    c = conn.cursor()

    try:
        keyfob_transactions = s.get("https://at.govt.nz/hop/cards/{}/transactions".format(card_id))

        transactions = []
        for t in keyfob_transactions.json()['Transactions']:
            try:
                tran = (card_id,
                        t['cardtransactionid'],
                        t['description'],
                        t['location'],
                        t['transactiondatetime'],
                        t['hop-balance-display'],
                        t['value'],
                        t['value-display'],
                        t['journey-id'],
                        t['refundrequested'],
                        t['refundable-value'],
                        t['transaction-type-description'],
                        t['transaction-type'])

                c.execute('INSERT INTO transactions (card_id,cardtransactionid, description, location, transactiondatetime, hop_balance_display, value, value_display, journey_id, refundrequested, refundable_value, transaction_type_description, transaction_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', tran)
                print("added {}".format(tran))
            except sqlite3.IntegrityError:
                pass

    except requests.HTTPError as err:
        print("http requests failed: {}".format(err), flush=True)
    except json.decoder.JSONDecodeError:
        print("failed to parse json: {}".format(keyfob_transactions.text), flush=True)

    conn.commit()


if __name__ == "__main__":
    print("starting...", flush=True)
    # in case we're restarting due to an error
    time.sleep(60)
    while True:
        ensure_database(DATABASE_FILE)

        conn = sqlite3.connect(DATABASE_FILE)

        for card_id in CARDS:
            print("scraping card: {}".format(card_id), flush=True)
            scrape_transactions_for_card(conn, card_id)

        conn.close()

        print("sleeping for {}s...".format(PERIOD), flush=True)
        time.sleep(PERIOD)
