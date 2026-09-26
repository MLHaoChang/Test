"""The FIFO ledger (plan 5.5): the lot book (`fifo.py`) and holdings over time (`holdings.py`).

Pure: it depends only on `core`, never reads or writes the registry, and gives the same result
for the same transactions. The import pipeline feeds it transactions and stores what it returns.
"""
