"""사용자 공용 테마 캐시와 기사 근거 중계."""
import asyncio
from datetime import datetime, timedelta
import time
import httpx
from app.config import settings
from app.schemas.theme_brief import ThemeBriefRequest, ThemeInput, Headline, LeaderInput, Evidence
from app.services.ai_service_auth import service_headers
from app.services.news_service import get_news_by_symbol
from app.services.theme_service import fetch_snapshot, KST


def recent_headlines(rows,now):
    previous=now.date()-timedelta(days=1)
    while previous.weekday()>4:previous-=timedelta(days=1)
    valid=[]
    for row in rows:
        try:
            at=datetime.strptime(row['date'],'%Y.%m.%d %H:%M').replace(tzinfo=KST)
            if not now-timedelta(days=7)<=at<=now:continue
            title=' '.join(row['title'].split())
            if not title:continue
            evidence=Evidence(title=title,source=row.get('source'),url=row.get('url'),published_at=at)
            priority=0 if at.date()==now.date() else 1 if at.date()==previous else 2
            valid.append((priority,at,evidence))
        except (ValueError,TypeError,KeyError):continue
    valid.sort(key=lambda x:(x[0],-x[1].timestamp()))
    seen=set();out=[]
    for _,_,e in valid:
        if e.title not in seen:out.append(e);seen.add(e.title)
    return out[:8]


async def collect_headlines(theme,now):
    async def one(leader):
        try:return (await get_news_by_symbol(leader.code,5))[:5]
        except Exception:return []
    rows=await asyncio.gather(*(one(leader) for leader in theme.leaders))
    return recent_headlines([r for group in rows for r in group],now)


async def request_themes(request):
    headers=service_headers()
    base=httpx.URL(settings.AI_SERVER_URL.strip())
    if base.scheme not in ('https','http') or not base.host:raise ValueError('Invalid AI origin')
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.post(str(base).rstrip('/')+'/brief/theme',headers=headers,json=request.model_dump(mode='json'))
        response.raise_for_status();return response.json()


class ThemeBriefService:
    def __init__(self,fetch=fetch_snapshot,collect=collect_headlines,generate=request_themes,clock=time.monotonic):
        self.fetch=fetch;self.collect=collect;self.generate=generate;self.clock=clock
        self.lock=asyncio.Lock();self.cached=None;self.last_good=None;self.last_attempt=float('-inf')
    async def get(self):
        async with self.lock:
            now=self.clock()
            if self.cached and now-self.cached[0]<self.cached[1]:return self.cached[2].model_copy(deep=True)
            try:snapshot=await self.fetch()
            except Exception:
                if self.last_good and self.clock()-self.last_good[0]<=3600:return self.last_good[1].model_copy(deep=True)
                raise ThemeUnavailable() from None
            inputs=[];evidence={}
            for direction,themes in (('rising',snapshot.rising),('falling',snapshot.falling)):
                for theme in themes:
                    rows=await self.collect(theme,datetime.now(KST))
                    evidence[theme.id]={str(i):e for i,e in enumerate(rows)}
                    if rows:inputs.append(ThemeInput(id=theme.id,name=theme.name,direction=direction,
                        change_rate=theme.change_rate,rise_count=theme.rise_count,fall_count=theme.fall_count,total_count=theme.total_count,
                        leaders=[LeaderInput(name=l.name,change_rate=l.change_rate) for l in theme.leaders],
                        headlines=[Headline(id=i,title=e.title,source=e.source,published_at=e.published_at) for i,e in evidence[theme.id].items()]))
            degraded=False
            if inputs:
                if now-self.last_attempt>=1800:
                    self.last_attempt=self.clock()
                    try:
                        data=await self.generate(ThemeBriefRequest(themes=inputs))
                        # AI 검증과 동일하게 제목 원문+고정 어미만 공개한다.
                        rows=data['themes'];expected={t.id for t in inputs}
                        if len(rows)!=len(expected) or {r['id'] for r in rows}!=expected:raise ValueError('Theme identity mismatch')
                        by_id={r['id']:r for r in rows}
                        for theme in snapshot.rising+snapshot.falling:
                            if theme.id not in by_id:continue
                            row=by_id[theme.id];reason=row.get('reason');ids=row.get('headline_ids')
                            if reason is None:continue
                            if not isinstance(reason,str) or len(reason)>80 or not isinstance(ids,list) or not 1<=len(ids)<=3 or len(set(ids))!=len(ids):continue
                            chosen=[evidence[theme.id][i] for i in ids]
                            if any(reason != e.title+' 소식이 있어요.' for e in chosen):continue
                            theme.reason=reason;theme.evidence=chosen
                    except Exception:
                        degraded=True
                        for theme in snapshot.rising+snapshot.falling:theme.reason=None;theme.evidence=[]
                else:degraded=True
            snapshot.generated_at=datetime.now(KST)
            self.last_good=(self.clock(),snapshot.model_copy(deep=True))
            self.cached=(self.clock(),300 if degraded else 1800,snapshot.model_copy(deep=True))
            return snapshot


class ThemeUnavailable(Exception):pass
service=ThemeBriefService()
