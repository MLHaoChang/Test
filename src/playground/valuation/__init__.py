"""The daily value of the portfolio in EUR (plan 5.7).

`series.py` is pure: it values a lot book with stored prices and ECB rates and never touches the
registry. `portfolio.py` reads what it needs from the registry, stores the series and its holdings
snapshots, and is what the CLI and the API call.
"""
