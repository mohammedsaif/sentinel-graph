# Video script: SENTINEL Graph (target 7–8 minutes)

## Before you hit record

1. Docker Desktop is running and Neo4j is healthy:
   ```bash
   cd C:\Users\Dell\Searches\sentinel-graph
   docker compose up -d
   docker ps
   ```
2. Empty the graph so the first load on camera starts from nothing. In Neo4j Browser
   (http://localhost:7474) run:
   ```cypher
   MATCH (n) DETACH DELETE n
   ```
   The constraints stay in place; the loader recreates them anyway (`IF NOT EXISTS`).
3. Open these and arrange them:
   * a terminal in `sentinel-graph`
   * VS Code (or any editor) with `load_graph.py` and `queries.cypher`
   * Neo4j Browser at http://localhost:7474, already signed in
   * `screenshots/cluster_D_9778c4c36d78e39f.png`
4. Make the terminal and editor font bigger (Ctrl + `+`) so text is readable in the recording.
5. Camera or mic on. **Do not show `.env` on screen**: it has your password.

---

## 1. The problem (0:00–1:00)

**Show:** your face, or the README title.

> "Hi, I'm ___. This is Project Work 6 for SENTINEL: finding fraud with a graph database.
>
> In the earlier projects we stored SENTINEL transactions in tables in Supabase. Tables are good
> at single rows: one payment, one amount, one label. But fraud often hides in *relationships*.
> A single payment can look normal, while the same phone is quietly being used by eight
> different customers. That is very hard to see in a table and very easy to see in a graph.
>
> So I loaded SENTINEL into Neo4j and wrote Cypher to find three patterns: shared devices,
> customers close to known fraud, and merchants that receive money from shared devices."

## 2. The graph model (1:00–1:45)

**Show:** the model block at the top of README.md.

> "The model has three kinds of nodes: Customer, Device and Merchant.
> A customer USED_DEVICE a device. A customer PAID a merchant. Every payment is one PAID edge,
> and it carries the amount, the time and the fraud label. I also store the device id on the
> PAID edge, so I know *when* each device was used, which I need for the 7-day rule."

## 3. Setup and connection (1:45–2:30)

**Show:** `docker-compose.yml`, then `.env.example`, then `common.py`.

> "Neo4j runs in Docker on port 7474 for the browser and 7687 for Bolt, which the Python
> driver uses. Secrets live in a .env file that is git-ignored. In the repo there's only
> .env.example with placeholder values.
>
> common.py opens the driver and calls verify_connectivity, so the script fails right away
> with a clear error if Neo4j isn't up."

**Run:**
```bash
docker ps
```
> "Here's the container, running and healthy."

## 4. The loader: MERGE and constraints (2:30–4:00)

**Show:** `load_graph.py`. Scroll to `CONSTRAINTS`, then `MERGE_PAID`, then the `usage` block in `main()`.

> "First, the constraints. Customer, Device and Merchant ids are unique, and there's a
> relationship constraint so each transaction id can exist on only one PAID edge.
>
> Every write uses MERGE, not CREATE. MERGE means 'find it, or create it if it's missing'. So if I
> run the load twice, the second run finds everything that's already there.
>
> One trap: if I did `tx_count = tx_count + 1`, a second run would double the counts. So I compute
> the totals in Python and SET them. Setting the same value twice changes nothing.
>
> The data is the SENTINEL export from Project Work 4 and 5. I take the earliest 50,000
> transactions and join each one to its fraud label. The script can also read straight
> from my Supabase load with --source supabase."

**Run (first time):**
```bash
python load_graph.py
```
> "50,000 payments: about 15,800 customers, 16,800 devices, 2,000 merchants, and 347
> fraud-labelled payments."

**Run (second time):**
```bash
python load_graph.py
```
> "Same command again. The final counts are identical: no duplicates."

**Optional, in Neo4j Browser:**
```cypher
SHOW CONSTRAINTS
```

## 5. The fraud queries (4:00–6:00)

**Show:** `queries.cypher`, then run in the terminal:
```bash
python run_queries.py
```
Scroll through the output as you talk.

**Query 1: shared devices in 7 days**
> "For each device I collect every payment with its customer and time. Each payment is treated
> as the start of a 7-day window, and I count distinct customers in that window.
> 29 devices had 3 or more customers within one week. The key number: payments made on shared
> devices are 4% fraud, against 0.69% overall. That's almost six times higher."

**Query 2: customers within 2 hops of fraud**
> "A fraud payment is an edge from a customer to a merchant. Two hops away are people who share a
> device with that payer, or who paid the same merchant. The merchant path is very wide, about
> 2,400 customers, because busy shops connect everyone. The device path is only about 300, and
> much more useful. At the top are customers with no fraud of their own who share a phone with
> several fraudsters. Those are the next accounts I'd review."

**Query 3: merchants paid from shared devices**
> "Every merchant in the top 15 sells electronics, gaming or gift cards: things that are easy to
> resell or turn into cash."

## 6. The suspicious cluster (6:00–7:30)

**Show:** Neo4j Browser. Run:
```cypher
:param device_id => 'D_9778c4c36d78e39f'
```
```cypher
MATCH (d:Device {device_id: $device_id})<-[u:USED_DEVICE]-(c:Customer)
OPTIONAL MATCH (c)-[p:PAID {device_id: d.device_id}]->(m:Merchant)
RETURN d, u, c, p, m
```
Drag a few nodes apart. Click a red/fraud PAID edge to show its properties.
You can also show `screenshots/cluster_D_9778c4c36d78e39f.png`, which marks the fraud edges in red.

> "This is the top cluster: one mobile phone in the middle and eight customers around it.
>
> These eight people have nothing in common. Ages go from 27 to 79, and they live in six
> different cities. Each customer appears on this phone for exactly one day, makes a burst of
> 5 to 9 payments to a single merchant, and is never seen on it again. Every merchant sells gift
> cards, electronics or gaming.
>
> That's the shape of account takeover: someone logs in to a stolen account, cashes out fast,
> and moves to the next one.
>
> Four of these 62 payments are labelled fraud: 6.5%, nine times the normal rate. All four are
> gift cards paid by card between 1 and 4 in the morning, India time. The other five customers
> have no fraud label yet, but they follow exactly the same pattern on the same phone. In a
> table, each of their payments looks fine. In the graph they're obviously part of the ring."

## 7. Wrap-up (7:30–8:00)

**Show:** the GitHub repo page, or the README.

> "To sum up: a graph turns hidden relationships into something you can query and see. MERGE
> and constraints make the load safe to repeat, and three short Cypher queries found shared
> devices, the next suspects, and the merchants fraudsters prefer.
> Everything's in the GitHub repo, with setup steps in the README. Thanks for watching."

---

## Tips

* If you go long, cut query 2's detail first. Keep the cluster explanation: it carries the most marks.
* Say numbers *after* they appear on screen, so the viewer can see them.
* It's fine to cut the recording between sections and join the clips afterwards.
