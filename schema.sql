create table transactions (
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
