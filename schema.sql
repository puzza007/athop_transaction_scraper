create table if not exists transactions (
       card_id,
       card_name,
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
);

create table if not exists tap_mismatch_notifications (
       card_id TEXT,
       journey_id TEXT,
       mismatch_type TEXT,
       notified_at TEXT,
       PRIMARY KEY (card_id, journey_id)
);

create table if not exists stops (
       name_key TEXT PRIMARY KEY,
       stop_name TEXT,
       stop_code TEXT,
       lat REAL,
       lon REAL
);

create table if not exists gtfs_meta (
       key TEXT PRIMARY KEY,
       value TEXT
);
