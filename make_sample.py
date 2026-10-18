import os
import random
import sqlite3

import pandas as pd

random.seed(42)
for f in ("source.db", "target.db"):
    if os.path.exists(f):
        os.remove(f)

countries = ["IN", "US", "UK", "DE"]
customers = [(i, f"Customer {i}", None if i in (4, 11) else f"user{i}@example.com",
              random.choice(countries), f"2024-01-{i:02d}") for i in range(1, 21)]
events = [(i, random.randint(1, 20), random.choice(["login", "view", "click", "purchase"]),
           f"2024-02-{random.randint(1, 28):02d} 10:00:00") for i in range(1, 101)]
orders = [(i, random.randint(1, 20),
           -25.0 if i in (7, 23, 41) else round(random.uniform(10, 500), 2),
           random.choice(["PAID", "PENDING", "CANCELLED"]),
           f"2024-03-{random.randint(1, 28):02d}") for i in range(1, 51)]


def make(path, ddl, inserts):
    con = sqlite3.connect(path)
    con.executescript(ddl)
    for sql, rows in inserts:
        con.executemany(sql, rows)
    con.commit()
    con.close()


make("source.db", """
CREATE TABLE customers (customer_id INTEGER, name TEXT, email TEXT, country TEXT, created_at TEXT);
CREATE TABLE events (event_id INTEGER, customer_id INTEGER, event_type TEXT, event_ts TEXT);
CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount REAL, status TEXT, order_date TEXT);
""", [
    ("INSERT INTO customers VALUES (?,?,?,?,?)", customers),
    ("INSERT INTO events VALUES (?,?,?,?)", events),
    ("INSERT INTO orders VALUES (?,?,?,?,?)", orders),
])

# Target: renamed tables/columns, one customer lost, negative amounts turned positive
make("target.db", """
CREATE TABLE dim_customer (customer_id INTEGER, name TEXT, email_address TEXT, country TEXT, created_at TEXT);
CREATE TABLE fact_orders (order_id INTEGER, customer_id INTEGER, order_amount REAL, status TEXT, order_date TEXT);
""", [
    ("INSERT INTO dim_customer VALUES (?,?,?,?,?)", [c for c in customers if c[0] != 15]),
    ("INSERT INTO fact_orders VALUES (?,?,?,?,?)", [(o[0], o[1], abs(o[2]), o[3], o[4]) for o in orders]),
])

rows = [
    ("customers", "",       "dim_customer", "",             "Count total rows in the table.",          "match"),
    ("customers", "email",  "dim_customer", "email_address", "Count rows with missing {col}.",          "match"),
    ("orders",    "",       "fact_orders",  "",             "Count total rows in the table.",          "match"),
    ("orders",    "amount", "fact_orders",  "order_amount", "Count rows with negative {col}.",         "match"),
    ("events",    "",       "",             "",             "Count events with missing customer or event type values.", "0"),
]
pd.DataFrame(rows, columns=["source_table", "source_column", "target_table",
                            "target_column", "rule", "expected"]).to_excel("mapping.xlsx", index=False)
print("Created source.db, target.db, mapping.xlsx")