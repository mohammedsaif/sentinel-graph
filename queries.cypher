// SENTINEL fraud-pattern queries.
// Paste any block into Neo4j Browser (http://localhost:7474), or run them all with
//   python run_queries.py
// In Browser, set the parameters first:
//   :param min_customers => 3
//   :param window_days => 7
//    devi:paramce_id => 'D_...'          (for the cluster view)

// name: q1_shared_devices_7_days
// Devices used by >= $min_customers distinct customers inside any $window_days window.
// Every payment is a possible window start; we keep each device's busiest window.
MATCH (d:Device)<-[:USED_DEVICE]-(:Customer)
WITH d, count(*) AS customers_ever
WHERE customers_ever >= $min_customers
MATCH (c:Customer)-[p:PAID {device_id: d.device_id}]->(:Merchant)
WITH d, customers_ever, collect({customer: c.customer_id, t: p.time, fraud: p.fraud_label}) AS uses
UNWIND uses AS start
WITH d, customers_ever, start.t AS window_start,
     [u IN uses WHERE start.t <= u.t < start.t + duration({days: $window_days})] AS win
UNWIND win AS u
WITH d, customers_ever, window_start,
     count(DISTINCT u.customer) AS customers_in_window,
     collect(DISTINCT u.customer) AS ids, count(u) AS payments, sum(u.fraud) AS fraud
WHERE customers_in_window >= $min_customers
// keep the busiest window per device
ORDER BY customers_in_window DESC, fraud DESC, window_start
WITH d, customers_ever,
     head(collect({n: customers_in_window, start: window_start, ids: ids,
                   payments: payments, fraud: fraud})) AS best
RETURN d.device_id AS device, d.kind AS kind,
       best.n AS customers_in_window, customers_ever,
       best.payments AS payments_in_window, best.fraud AS fraud_in_window,
       toString(date(best.start)) AS window_start,
       toString(date(best.start + duration({days: $window_days}))) AS window_end
ORDER BY customers_in_window DESC, fraud_in_window DESC, device
LIMIT 25;

// name: q1_summary
// Total number of devices that pass the rule above.
MATCH (d:Device)<-[:USED_DEVICE]-(:Customer)
WITH d, count(*) AS customers_ever WHERE customers_ever >= $min_customers
MATCH (c:Customer)-[p:PAID {device_id: d.device_id}]->(:Merchant)
WITH d, collect({customer: c.customer_id, t: p.time}) AS uses
UNWIND uses AS start
WITH d, start.t AS window_start,
     [u IN uses WHERE start.t <= u.t < start.t + duration({days: $window_days}) | u.customer] AS win
UNWIND win AS cid
WITH d, window_start, count(DISTINCT cid) AS n
WITH d, max(n) AS busiest
WHERE busiest >= $min_customers
RETURN count(d) AS devices_flagged, max(busiest) AS max_customers_in_window;

// name: q2_customers_near_fraud
// Customers within 2 hops of a fraud-labelled PAID edge (customer)-[PAID {fraud_label:1}]->(merchant):
//   * shared device : fraud payer -[USED_DEVICE]-> device <-[USED_DEVICE]- customer   (2 hops)
//   * same merchant : customer -[PAID]-> merchant that received the fraud payment    (1 hop from the edge)
// The payer is excluded. Customers with no fraud of their own but a shared device are the
// strongest "next to investigate" signal, so they sort first.
MATCH (f:Customer)-[:PAID {fraud_label: 1}]->(fm:Merchant)
WITH collect(DISTINCT f) AS fraud_payers, collect(DISTINCT fm) AS fraud_merchants
CALL (fraud_payers, fraud_merchants) {
  UNWIND fraud_payers AS f
  MATCH (f)-[:USED_DEVICE]->(d:Device)<-[:USED_DEVICE]-(c:Customer)
  WHERE c <> f
  RETURN c, 'device' AS via, f.customer_id + ' via ' + d.device_id AS link
  UNION
  UNWIND fraud_merchants AS m
  MATCH (c:Customer)-[:PAID]->(m)
  RETURN c, 'merchant' AS via, m.merchant_id AS link
}
WITH c, via, count(DISTINCT link) AS links, collect(DISTINCT link)[..3] AS examples
WITH c,
     sum(CASE via WHEN 'device' THEN links ELSE 0 END) AS shared_device_links,
     sum(CASE via WHEN 'merchant' THEN links ELSE 0 END) AS fraud_merchant_links,
     reduce(acc = [], e IN collect(CASE via WHEN 'device' THEN examples END) | acc + e) AS device_examples
OPTIONAL MATCH (c)-[own:PAID {fraud_label: 1}]->()
WITH c, shared_device_links, fraud_merchant_links, device_examples, count(own) AS own_fraud
RETURN c.customer_id AS customer, own_fraud, shared_device_links, fraud_merchant_links,
       device_examples[..2] AS linked_fraud_payer_via_device
ORDER BY (own_fraud = 0 AND shared_device_links > 0) DESC, shared_device_links DESC,
         fraud_merchant_links DESC, customer
LIMIT 25;

// name: q2_summary
// How many customers sit within 2 hops of fraud, split by path type.
MATCH (f:Customer)-[:PAID {fraud_label: 1}]->(fm:Merchant)
WITH collect(DISTINCT f) AS fraud_payers, collect(DISTINCT fm) AS fraud_merchants
CALL (fraud_payers) {
  UNWIND fraud_payers AS f
  MATCH (f)-[:USED_DEVICE]->(:Device)<-[:USED_DEVICE]-(c:Customer) WHERE c <> f
  RETURN count(DISTINCT c) AS via_shared_device
}
CALL (fraud_merchants) {
  UNWIND fraud_merchants AS m
  MATCH (c:Customer)-[:PAID]->(m)
  RETURN count(DISTINCT c) AS via_fraud_merchant
}
RETURN size(fraud_payers) AS fraud_payers, size(fraud_merchants) AS fraud_merchants,
       via_shared_device, via_fraud_merchant;

// name: q3_merchants_paid_from_shared_devices
// Merchants that most often receive payments made on a shared device
// (a device used by >= $min_customers distinct customers in the sample).
MATCH (d:Device)<-[:USED_DEVICE]-(:Customer)
WITH d, count(*) AS customers WHERE customers >= $min_customers
MATCH (c:Customer)-[p:PAID {device_id: d.device_id}]->(m:Merchant)
RETURN m.merchant_id AS merchant, m.name AS name, m.category AS category, m.city AS city,
       count(p) AS payments_from_shared_devices,
       count(DISTINCT d) AS shared_devices,
       count(DISTINCT c) AS customers,
       sum(p.fraud_label) AS fraud_payments,
       round(sum(p.amount), 2) AS amount_inr
ORDER BY payments_from_shared_devices DESC, fraud_payments DESC, merchant
LIMIT 15;

// name: q4_most_suspicious_cluster
// Picks the shared device to screenshot: most customers, then most fraud on that device.
MATCH (d:Device)<-[:USED_DEVICE]-(c:Customer)
WITH d, count(c) AS customers WHERE customers >= $min_customers
MATCH (:Customer)-[p:PAID {device_id: d.device_id}]->(m:Merchant)
RETURN d.device_id AS device, d.kind AS kind, customers,
       count(p) AS payments, sum(p.fraud_label) AS fraud_payments,
       count(DISTINCT m) AS merchants,
       min(p.time) AS first_payment, max(p.time) AS last_payment
ORDER BY fraud_payments DESC, customers DESC, device
LIMIT 5;

// name: q5_cluster_graph
// Graph view for one cluster (run in Browser with :param device_id => '<device from q4>').
// Shows the device, every customer on it, and the merchants they paid from that device.
MATCH (d:Device {device_id: $device_id})<-[u:USED_DEVICE]-(c:Customer)
OPTIONAL MATCH (c)-[p:PAID {device_id: d.device_id}]->(m:Merchant)
RETURN d, u, c, p, m;
