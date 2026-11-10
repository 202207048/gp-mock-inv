"""종목별 뉴스 근거 중계와 프로세스 공용 캐시."""
import asyncio
import html
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import logging
import re
import time
from weakref import WeakValueDictionary
import httpx
from app.config import settings
from app.schemas.stock_brief import StockBriefRequest, StockInput, Headline, StockPublicResponse, RelatedStock, Evidence
from app.services.ai_service_auth import service_headers
from app.services.news_service import get_news_by_symbol
from app.services.theme_service import KST, number
from app.services.theme_brief_service import collect_market_context, news_time, session_dates, GENERIC_TITLES, SPECULATIVE_TITLES
from app.services.stock_brief_validation import sanitize_stock_output

from app.services.brief_store import load_stock, save_stock

logger=logging.getLogger(__name__)
NAVER='https://m.stock.naver.com/api/stock/'

async def fetch_quote(code):
    async with httpx.AsyncClient(timeout=15,headers={'User-Agent':'Mozilla/5.0'}) as client:
        response=await client.get(NAVER+code+'/basic');response.raise_for_status();row=response.json()
    at=datetime.fromisoformat(row['localTradedAt'])
    if at.utcoffset() is None:raise ValueError('Missing quote timezone')
    return dict(name=row['stockName'],price=int(number(row['closePrice'])),change_rate=float(number(row['fluctuationsRatio'])),as_of=at.astimezone(KST))

def select_related(rows,code,rate):
    found={}
    for row in rows:
        try:
            key=str(row['itemCode'])
            if key==code or not re.fullmatch(r'\d{6}',key) or key in found:continue
            found[key]=RelatedStock(code=key,name=row['stockName'],price=int(number(row['closePrice'])),change_rate=float(number(row['fluctuationsRatio'])))
        except (ValueError,TypeError,KeyError):continue
    return sorted(found.values(),key=lambda x:(not (x.change_rate*rate>0),-abs(x.change_rate),x.code))[:4]

async def fetch_related(code,rate):
    async with httpx.AsyncClient(timeout=15,headers={'User-Agent':'Mozilla/5.0'}) as client:
        response=await client.get(NAVER+code+'/integration');response.raise_for_status()
    return select_related(response.json().get('industryCompareInfo',[]),code,rate)

def article_inputs(rows,prefix,now,as_of,notes=False):
    dates=session_dates(now,as_of);out={}
    for row in rows:
        try:
            at=news_time(row.get('date'),now);title=' '.join(html.unescape(row['title']).split())
            if at is None or not now-timedelta(days=3)<=at<=now:continue
            if not title or any(x.replace(' ','') in title.replace(' ','') for x in GENERIC_TITLES) or any(x in title for x in SPECULATIVE_TITLES):continue
            if title in {h.title for h,e in out.values()}:continue
            key=prefix+str(len(out))
            evidence=Evidence(title=title,source=row.get('source'),url=row.get('url'),published_at=at)
            # 본 종목 기사는 앞부분을 붙여 상세 화면 제목을 제목보다 자세히 쓸 수 있게 한다.
            note=' '.join(html.unescape(row.get('summary') or '').split()).rstrip('.… ')[:200] if notes else ''
            out[key]=(Headline(id=key,title=title,source=evidence.source,published_at=at,in_move_window=at.date() in dates,summary=note or None),evidence)
        except (ValueError,TypeError,KeyError):continue
    return out

async def collect_articles(code,related,now,as_of):
    async def one(symbol,limit):
        try:return (await get_news_by_symbol(symbol,limit))[:limit]
        except Exception:return []
    groups=await asyncio.gather(one(code,10),*(one(s.code,5) for s in related))
    own=article_inputs(groups[0],'s-',now,as_of,notes=True)
    peers={s.code:article_inputs(rows,'p'+s.code+'-',now,as_of) for s,rows in zip(related,groups[1:])}
    try:_,market=await collect_market_context(now,as_of)
    except Exception:market={}
    dates=session_dates(now,as_of)
    common={i:(Headline(id=i,title=e.title,source=e.source,published_at=e.published_at,
        in_move_window=e.published_at.astimezone(KST).date() in dates),Evidence(**e.model_dump()))
        for i,e in market.items() if now-timedelta(days=3)<=e.published_at<=now and not any(x in e.title for x in SPECULATIVE_TITLES)}
    return own,peers,common

async def request_stock(request):
    headers=service_headers();base=httpx.URL(settings.AI_SERVER_URL.strip())
    if base.scheme not in ('http','https') or not base.host:raise ValueError('Invalid AI origin')
    async with httpx.AsyncClient(timeout=30) as client:
        url=str(base).rstrip('/')+'/brief/stock';payload=request.model_dump(mode='json')
        response=await client.post(url,headers=headers,json=payload)
        if response.status_code==422:
            # 기사 앞부분(summary) 칸을 모르는 이전 AI 서버는 422를 돌려준다. 그 칸을 빼고 한 번 더 보낸다.
            for h in [h for s in [payload['stock'],*payload['related']] for h in s['headlines']]+payload['market_headlines']:h.pop('summary',None)
            response=await client.post(url,headers=headers,json=payload)
        response.raise_for_status();return response.json()

class StockUnavailable(Exception):pass

class StockBriefService:
    def __init__(self,quote=fetch_quote,related=fetch_related,collect=collect_articles,generate=request_stock,clock=time.monotonic,load=load_stock,save=save_stock):
        self.quote=quote;self.related=related;self.collect=collect;self.generate=generate;self.clock=clock
        self.load=load;self.save=save
        self.cache=OrderedDict();self.locks=WeakValueDictionary()
    async def get(self,code):
        lock=self.locks.setdefault(code,asyncio.Lock())
        async with lock:
            old=self.cache.get(code);fresh=True
            if old is None:
                try:
                    stored=await asyncio.to_thread(self.load,code)
                    if stored:
                        payload,created_at=stored
                        # SQLite는 timezone 정보를 보존하지 않아 UTC로 복원한다.
                        if created_at.tzinfo is None:created_at=created_at.replace(tzinfo=timezone.utc)
                        age=(datetime.now(timezone.utc)-created_at).total_seconds()
                        snapshot=StockPublicResponse.model_validate(payload)
                        if 0<=age<10800 and snapshot.symbol_code==code and snapshot.ai_available and "citations" in payload and "banner_summary" in payload and "banner_detail" in payload and "banner_tone" in payload and all("relation" in p for p in payload.get("related",[])):
                            old=(self.clock()-age,10800,snapshot)
                            self.cache[code]=old
                            while len(self.cache)>200:self.cache.popitem(last=False)
                except Exception:logger.warning('brief_store operation=restore_stock failed')
            try:quote=await self.quote(code)
            except Exception:
                fresh=False
                if not old:raise StockUnavailable() from None
                quote={k:getattr(old[2],k) for k in ('name','price','change_rate','as_of')}
            now=self.clock()
            direction=lambda rate:(rate>0)-(rate<0)
            changed=old and direction(old[2].change_rate)!=direction(quote['change_rate']) and now-old[0]>=1800
            if old and (not fresh or (now-old[0]<old[1] and not changed)):
                result=old[2].model_copy(deep=True)
                for k,v in quote.items():setattr(result,k,v)
                return result
            wall=datetime.now(KST)
            try:related=await self.related(code,quote['change_rate'])
            except Exception:related=[]
            snapshot=StockPublicResponse(symbol_code=code,**quote,related=related,generated_at=wall)
            try:own,peers,market=await self.collect(code,related,wall,quote['as_of'])
            except Exception:own,peers,market={},{},{}
            def stock(symbol,name,rate,articles):return StockInput(code=symbol,name=name,change_rate=rate,explain_move=abs(rate)>=1,headlines=[h for h,e in articles.values()])
            request=StockBriefRequest(stock=stock(code,quote['name'],quote['change_rate'],own),
                related=[stock(s.code,s.name,s.change_rate,peers.get(s.code,{})) for s in related],market_headlines=[h for h,e in market.values()])
            evidence={**own,**market}
            for articles in peers.values():evidence.update(articles)
            ttl=1800;diagnostic='no_recent_news'
            if evidence:
                ttl=10800
                try:
                    raw=await self.generate(request)
                    data=sanitize_stock_output(raw,request)
                    # 배너 요약을 모르는 이전 AI 서버의 응답은 저장하지 않고 30분 뒤 다시 만든다.
                    if 'banner_summary' not in raw:ttl=1800
                    snapshot.title=data['title']
                    snapshot.banner_summary=data['banner_summary']
                    snapshot.banner_detail=data['banner_detail']
                    snapshot.banner_tone=data['banner_tone']
                    ids=list(data['title_ids'])
                    for field in ('recent_issues','positive_factors','risk_factors'):
                        setattr(snapshot,field,[x['text'] for x in data[field]])
                        ids.extend(i for x in data[field] for i in x['ids'])
                        for row in data[field]:
                            sources = snapshot.citations.setdefault(row['text'], [])
                            for article_id in row['ids']:
                                article = evidence[article_id][1]
                                if article not in sources:
                                    sources.append(article)
                    by_code={x['code']:x for x in data['related']}
                    for peer in snapshot.related:
                        row=by_code[peer.code];peer.title=row['title'];ids.extend(row['ids'])
                        peer.banner_summary,peer.banner_tone,peer.relation=row['banner_summary'],row['banner_tone'],row['relation'];ids.extend(row['banner_ids'])
                    ids.extend(data['banner_ids'])
                    snapshot.evidence=[evidence[i][1] for i in dict.fromkeys(ids)][:5]
                    snapshot.ai_available=bool(snapshot.title or snapshot.banner_summary or snapshot.recent_issues or snapshot.positive_factors or snapshot.risk_factors or any(p.title for p in snapshot.related))
                    diagnostic='accepted' if snapshot.ai_available else 'ai_abstained'
                except Exception:ttl=300;diagnostic='validation_failed:upstream_error'
            logger.info('stock_diagnostic code=%s result=%s',code,diagnostic)
            self.cache[code]=(self.clock(),ttl,snapshot.model_copy(deep=True));self.cache.move_to_end(code)
            while len(self.cache)>200:self.cache.popitem(last=False)
            if ttl==10800 and snapshot.ai_available:
                try:await asyncio.to_thread(self.save,code,snapshot.model_dump(mode='json'))
                except Exception:logger.warning('brief_store operation=persist_stock failed')
            return snapshot

service=StockBriefService()
