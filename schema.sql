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
