"""공개 테마 시세를 결정적 규칙으로 선별한다."""
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import httpx
from app.schemas.theme_brief import Theme, Leader, ThemePublicResponse
KST=timezone(timedelta(hours=9))
URL="https://m.stock.naver.com/api/stocks/theme"


def number(value):
    result=Decimal(str(value).replace(",",""))
    if not result.is_finite():raise ValueError("Invalid quote")
    return result


def select_themes(groups):
    themes=[]
    for g in groups:
        theme=Theme(id=str(g['no']),name=g['name'],change_rate=number(g['changeRate']),
            rise_count=int(g['riseCount']),fall_count=int(g['fallCount']),steady_count=int(g['steadyCount']),
            total_count=int(g['totalCount']),leaders=[])
        if theme.total_count>=5:themes.append(theme)
    rising=sorted((t for t in themes if t.change_rate>0 and t.rise_count>t.fall_count),key=lambda t:(-t.change_rate,t.id))[:3]
    falling=sorted((t for t in themes if t.change_rate<0 and t.fall_count>t.rise_count),key=lambda t:(t.change_rate,t.id))[:3]
    return rising,falling


def select_leaders(stocks,direction):
    rows=[Leader(code=str(s['itemCode']),name=s['stockName'],price=number(s['closePrice']),
                 change_rate=number(s['fluctuationsRatio'])) for s in stocks]
    return sorted(rows,key=lambda x:((-x.change_rate if direction=='rising' else x.change_rate),x.code))[:3]


async def fetch_snapshot():
    async with httpx.AsyncClient(timeout=15,headers={'User-Agent':'Mozilla/5.0'}) as client:
        async def get(url,page):
            r=await client.get(url,params={'page':page,'pageSize':100});r.raise_for_status();return r.json()
        pages=await asyncio.gather(*(get(URL,p) for p in (1,2,3)))
        if max(int(p['totalCount']) for p in pages)>300:raise ValueError("Theme pagination changed")
        rising,falling=select_themes([g for p in pages for g in p['groups']])
        stamps=[]
        for direction,themes in (('rising',rising),('falling',falling)):
            for theme in themes:
                detail=await get(URL+'/'+theme.id,1)
                stocks=list(detail['stocks'])
                for page in range(2,(int(detail['totalCount'])+99)//100+1):stocks.extend((await get(URL+'/'+theme.id,page))['stocks'])
                theme.leaders=select_leaders(stocks,direction)
                codes={s.code for s in theme.leaders}
                for stock in stocks:
                    if str(stock['itemCode']) in codes:
                        value=datetime.fromisoformat(stock['localTradedAt'])
                        if value.utcoffset() is None:raise ValueError("Missing market timezone")
                        stamps.append(value.astimezone(KST))
        now=datetime.now(KST)
        return ThemePublicResponse(as_of=max(stamps) if stamps else now,generated_at=now,rising=rising,falling=falling)
