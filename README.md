# CleanPO

Customer orders in, clean ERP import out.

CleanPO reads the purchase orders that a wholesale distributor receives (PDF, e-mail, Excel,
CSV, photo). It matches each line to the distributor's own product codes, customer accounts
and prices. It checks the order, and it makes one import file for the ERP. Orders with a
problem go to a review screen with the reason. A dashboard shows the time and money saved.

All data in this repository is synthetic. The distributor "Lone Pine Food Supply", its
customers, products and prices are made up.

## How an order moves

1. **Read.** The Claude API reads the file and returns the customer, the lines, the quantities
   and the prices in a fixed JSON structure.
2. **Match.** Python code finds the customer (sender e-mail, then name) and each product
   (exact code, the customer's own code, the memory list, then name similarity).
   When two products are possible, Claude can choose from the candidates, but only when the
   text or the order history makes the choice clear.
3. **Check.** Code (not AI) checks prices against the price list, finds duplicate orders,
   unusual quantities, discontinued items, unknown customers and accounts on credit hold.
   It also converts units, for example 60 cans to 10 cases.
4. **Sort.** Orders without a problem are ready. Orders with a problem wait for a human.
   E-mails that are not orders are kept out.
5. **Review.** The reviewer sees the original document next to the reading and solves each
   problem. The choices go into the memory list, so the same text matches next time.
6. **Export.** One CSV import file for all ready orders, and a summary workbook.

## Run the demo

```
pip install -r requirements.txt
python scripts/make_demo_data.py
streamlit run app.py
```

Then open "Process orders" and click "Load sample orders".

Without an API key the app runs in demo mode: it uses saved readings of the 16 sample orders
(`demo_data/readings`). To read new files, set the `ANTHROPIC_API_KEY` environment variable
before you start the app. Do not type the key into the app or into a file.

On the first start, the app adds six weeks of synthetic history, so that the dashboard shows
trends. A note under the dashboard shows while this history exists. "Settings > Remove sample
history" deletes it.

## Tests

```
python -m pytest -q
```

The tests process the 16 sample orders and compare each result with `demo_data/expected.json`:
8 ready orders, 7 orders with one planted problem each, and 1 e-mail that is not an order.

## Time saved

Time saved = (handled orders x manual minutes per order) - real review time.
The manual minutes per order come from the client: the clerk times 10 orders by hand during
the trial. The hourly rate default is the May 2025 BLS median wage for order clerks
(USD 22.20). Both values are in Settings.

## Layout

```
app.py                     Streamlit app (dashboard, process, review, export, settings)
cleanpo/reader.py          Claude API reading, structured output, replay mode
cleanpo/matcher.py         customer and product matching
cleanpo/pipeline.py        checks, sorting, approve and reject
cleanpo/export.py          import CSV and summary workbook
cleanpo/metrics.py         dashboard numbers and synthetic demo history
cleanpo/db.py              DuckDB tables
scripts/make_demo_data.py  synthetic distributor, customers, products and 16 orders
demo_data/                 the generated demo data
```

## Status

- Tested in replay mode (22 tests pass).
- The Claude API reading path is written to the current API documentation but has not yet run
  against the live API. Test it with an API key before a client demo.
- The import layout is a generic sales-order CSV. Change it to the client's ERP layout during setup.
