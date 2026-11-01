"""사용자 공용 테마 캐시와 기사 근거 중계."""
import asyncio
from datetime import datetime, timedelta
import time
import logging
import re
import httpx
from app.config import settings
from app.schemas.theme_brief import ThemeBriefRequest, ThemeInput, Headline, LeaderInput, Evidence, MarketContext, MarketIndex
from app.services.ai_service_auth import service_headers
from app.services.news_service import get_news_by_symbol, get_market_news, parse_market_news, MARKET_NEWS_URL
from app.services.kis_service import get_index_price
from app.services.theme_service import fetch_snapshot, KST


logger=logging.getLogger(__name__)
GENERIC_TITLES=('오늘의 메모','다이제스트','프리미엄9','보호예수 해제')
SPECULATIVE_TITLES=('?','？','전망','기대','예상','목표가','주목')


def news_time(value,now):
    text=str(value or '').strip()
    relative=re.fullmatch(r'(\d+)\s*(분|시간)전',text)
    if relative:return now-timedelta(**{'minutes' if relative[2]=='분' else 'hours':int(relative[1])})
    if text=='방금전':return now
    try:return datetime.strptime(text,'%Y.%m.%d %H:%M').replace(tzinfo=KST)
    except ValueError:pass
    try:
        at=datetime.fromisoformat(text)
        return at.replace(tzinfo=KST) if at.utcoffset() is None else at.astimezone(KST)
    except ValueError:return None


def session_dates(now,anchor=None):
    # 시세 기준 거래일로 기사 기간을 맞춘다. 주말에 장 마감 뒤 기사(해외 증시 등)가 섞이지 않게 한다.
    session=(anchor or now).astimezone(KST).date()
    previous=session-timedelta(days=1)
    while previous.weekday()>4:previous-=timedelta(days=1)
    return session,previous


def recent_headlines(rows,now,*,market=False,anchor=None):
    session,previous=session_dates(now,anchor)
    valid=[]
    for row in rows:
        try:
            at=news_time(row.get('date'),now)
            if at is None or at>now or at.date() not in (session,previous):continue
            title=' '.join(row['title'].split())
            if not title or any(pattern.replace(' ','') in title.replace(' ','') for pattern in GENERIC_TITLES):continue
            if any(marker in title for marker in SPECULATIVE_TITLES):continue
            evidence=Evidence(title=title,source=row.get('source'),url=row.get('url'),published_at=at)
            priority=0 if at.date()==session else 1 if at.date()==previous else 2
            valid.append((priority,at,evidence))
        except (ValueError,TypeError,KeyError):continue
    valid.sort(key=lambda x:(x[0],not bool(re.search('코스피|코스닥|증시|급락|급등',x[2].title)) if market else False,-x[1].timestamp()))
    seen=set();out=[]
    for _,_,e in valid:
        if e.title not in seen:out.append(e);seen.add(e.title)
    return out[:8]


async def collect_headlines(theme,now,anchor=None):
    async def one(leader):
        try:return (await get_news_by_symbol(leader.code,5))[:5]
        except Exception:return []
    rows=await asyncio.gather(*(one(leader) for leader in theme.leaders))
    return recent_headlines([r for group in rows for r in group],now,anchor=anchor)


async def market_news_on(day):
    # 지난 거래일(주말·장 마감 다음 날) 시장 기사는 날짜 지정 목록에서 가져온다.
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response=await client.get(MARKET_NEWS_URL,params={'date':day.strftime('%Y%m%d')},headers={'User-Agent':'Mozilla/5.0'},timeout=10)
    response.raise_for_status()
    return parse_market_news(response.text)


async def collect_market_context(now,anchor=None):
    indices=[]
    for name,code in (('코스피','0001'),('코스닥','1001')):
        try:
            quote=await get_index_price(code)
            if quote and quote.get('change_rate') is not None:indices.append(MarketIndex(name=name,change_rate=quote['change_rate']))
        except Exception:pass
    session,_=session_dates(now,anchor)
    try:rows=await (get_market_news(80) if session==now.date() else market_news_on(session))
    except Exception:rows=[]
    today=[r for r in rows if (at:=news_time(r.get('date'),now)) is not None and at.date()==session]
    today.sort(key=lambda r:not bool(re.search('코스피|코스닥|증시|급락|급등',r.get('title',''))))
    evidence=recent_headlines(today,now,market=True,anchor=anchor)
    by_id={'m-'+str(i):e for i,e in enumerate(evidence)}
    context=MarketContext(indices=indices,headlines=[Headline(id=i,title=e.title,source=e.source,published_at=e.published_at) for i,e in by_id.items()])
    return context,by_id


async def request_themes(request):
    headers=service_headers()
    base=httpx.URL(settings.AI_SERVER_URL.strip())
    if base.scheme not in ('https','http') or not base.host:raise ValueError('Invalid AI origin')
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.post(str(base).rstrip('/')+'/brief/theme',headers=headers,json=request.model_dump(mode='json'))
        response.raise_for_status();return response.json()


class ThemeBriefService:
    def __init__(self,fetch=fetch_snapshot,collect=collect_headlines,generate=request_themes,clock=time.monotonic,market=collect_market_context):
        self.fetch=fetch;self.collect=collect;self.generate=generate;self.clock=clock
        self.market=market;self.diagnostics={}
        self.lock=asyncio.Lock();self.cached=None;self.last_good=None;self.last_attempt=float('-inf')
    async def get(self):
        async with self.lock:
            now=self.clock()
            if self.cached and now-self.cached[0]<self.cached[1]:return self.cached[2].model_copy(deep=True)
            try:snapshot=await self.fetch()
            except Exception:
                if self.last_good and self.clock()-self.last_good[0]<=3600:return self.last_good[1].model_copy(deep=True)
                raise ThemeUnavailable() from None
            try:market,market_evidence=await self.market(datetime.now(KST),snapshot.as_of)
            except Exception:market,market_evidence=MarketContext(),{}
            inputs=[];evidence={};self.diagnostics={}
            for direction,themes in (('rising',snapshot.rising),('falling',snapshot.falling)):
                for theme in themes:
                    rows=await self.collect(theme,datetime.now(KST),snapshot.as_of)
                    local={'t'+theme.id+'-'+str(i):e for i,e in enumerate(rows)}
                    evidence[theme.id]={**market_evidence,**local}
                    if rows or market.headlines:inputs.append(ThemeInput(id=theme.id,name=theme.name,direction=direction,
                        change_rate=theme.change_rate,rise_count=theme.rise_count,fall_count=theme.fall_count,total_count=theme.total_count,
                        leaders=[LeaderInput(name=l.name,change_rate=l.change_rate) for l in theme.leaders],
                        headlines=[Headline(id=i,title=e.title,source=e.source,published_at=e.published_at) for i,e in local.items()]))
                    else:self.diagnostics[theme.id]='no_recent_news'
            degraded=False
            if inputs:
                if now-self.last_attempt>=1800:
                    self.last_attempt=self.clock()
                    try:
                        data=await self.generate(ThemeBriefRequest(themes=inputs,market=market))
                        # 공통시장과 테마 인용을 원래 근거 기사로 복원한다.
                        rows=data['themes'];expected={t.id for t in inputs}
                        if len(rows)!=len(expected) or {r['id'] for r in rows}!=expected:raise ValueError('Theme identity mismatch')
                        by_id={r['id']:r for r in rows}
                        for theme in snapshot.rising+snapshot.falling:
                            if theme.id not in by_id:continue
                            row=by_id[theme.id];reason=row.get('reason');ids=row.get('headline_ids')
                            if reason is None:
                                self.diagnostics[theme.id]='ai_abstained';continue
                            if not isinstance(reason,str) or len(reason)>80 or not isinstance(ids,list) or not 1<=len(ids)<=3 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or any(i not in evidence[theme.id] for i in ids):
                                self.diagnostics[theme.id]='validation_failed:headline_ids';continue
                            chosen=[evidence[theme.id][i] for i in ids]
                            theme.reason=reason;theme.evidence=chosen
                            self.diagnostics[theme.id]='accepted'
                    except Exception:
                        degraded=True
                        for theme in snapshot.rising+snapshot.falling:
                            theme.reason=None;theme.evidence=[]
                            self.diagnostics[theme.id]='validation_failed:upstream_error'
                else:
                    degraded=True
                    for theme in inputs:self.diagnostics[theme.id]='ai_abstained'
            for identity,code in self.diagnostics.items():logger.info('theme_diagnostic id=%s code=%s',identity,code)
            snapshot.generated_at=datetime.now(KST)
            self.last_good=(self.clock(),snapshot.model_copy(deep=True))
            self.cached=(self.clock(),300 if degraded else 1800,snapshot.model_copy(deep=True))
            return snapshot


class ThemeUnavailable(Exception):pass
service=ThemeBriefService()
