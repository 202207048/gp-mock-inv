"""Shared symbol verification and transactional registration for orders."""
from fastapi import HTTPException

from app.models.security import ItemMaster
from app.services import kis_service as kis


async def resolve_listed_stock(symbol_code: str, *, quote: dict | None = None) -> dict | None:
    """Resolve a domestic symbol; reuse a fetched quote and match search codes exactly."""
    code = (symbol_code or "").strip()
    if len(code) != 6 or any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in code):
        return None
    if quote is None:
        listed = await kis.get_listed_stock(code)
        if listed and str(listed.get("name") or "").strip():
            return listed
    else:
        name = str(quote.get("name") or "").strip()
        if name:
            return {"symbol_code": code, "name": name, "market_type": "국내주식", "sector_code": None}
    for item in await kis.search_listed_stocks(code, 5):
        if item.get("symbol_code") == code and str(item.get("name") or "").strip():
            return item
    return None


def ensure_security(db, symbol_code: str, name: str | None, sector_code: str | None = None) -> ItemMaster:
    """Register verified metadata in the order transaction, tolerating concurrent orders."""
    security = db.get(ItemMaster, symbol_code)
    if security is not None:
        return security
    stock_name = (name or "").strip()
    if not stock_name:
        raise HTTPException(404, "종목을 찾을 수 없습니다.")
    values = dict(symbol_code=symbol_code, name=stock_name[:100], market_type="국내주식",
                  sector_code=((sector_code or "").strip()[:20] or None))
    dialect = db.get_bind().dialect.name
    if dialect in ("postgresql", "sqlite"):
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        db.execute(insert(ItemMaster).values(**values).on_conflict_do_nothing(index_elements=["symbol_code"]))
        return db.get(ItemMaster, symbol_code)
    security = ItemMaster(**values)
    db.add(security)
    db.flush()
    return security
