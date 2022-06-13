# Store AT Hop card transactions in an sqlite database

## Running

Set environment variables

```shell
export AT_USERNAME=athopaccount@gmail.com
export AT_PASSWORD=1234password
export AT_CARDS=7824670200000000001,7824670200000000002
export AT_DATABASE_FILE=/data/athop.db
export AT_PERIOD=3600
# only set slack stuff if you want notifications
export AT_SLACK_API_TOKEN=xoxp-7626728587-34789439-74538973
export AT_SLACK_CHANNEL='#notifications'
```

Create an SSH key for litestream backup to remote server (you may not
want this in which case edit docker-compose to remove the litestream
section)

```shell
❯ ssh-keygen
Generating public/private rsa key pair.
Enter file in which to save the key (~/.ssh/id_rsa): ~/athop_transaction_scraper/litestream_rsa
...
```

Edit `litestream.yml`

Start container

```shell
docker-compose up -d
```

Your data should now be in `./data/athop.db`
