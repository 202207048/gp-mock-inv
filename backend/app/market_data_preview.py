"""Local read-only market data server; no accounts, database, or order workers.

Run: python -m uvicorn app.market_data_preview:app --host 127.0.0.1 --port 8011
The Vite dev proxy routes only analysis/fundamentals here. Production uses
the same services through app.routers.securities.
"""
from fastapi import FastAPI
from app.services.stock_analysis import get_stock_analysis
from app.services.stock_fundamentals import get_stock_fundamentals
from app.services.stock_ratings import get_stock_ratings

app = FastAPI(title='Local stock analysis preview')


@app.get('/api/stocks/{code}/ratings')
async def ratings(code: str):
    return await get_stock_ratings(code)


@app.get('/api/stocks/{code}/analysis')
async def analysis(code: str):
    return await get_stock_analysis(code)


@app.get('/api/stocks/{code}/fundamentals')
async def fundamentals(code: str):
    return await get_stock_fundamentals(code)
