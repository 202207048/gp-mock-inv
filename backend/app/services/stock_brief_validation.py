from decimal import Decimal, InvalidOperation
import logging
import re
logger=logging.getLogger(__name__)
FORBIDDEN=re.compile(r"전망|기대|예상|예측|목표가|추천|매수하세요|매도하세요|사세요|파세요|할\s*것|때문에|영향으로|탓에")
NUMBERS=re.compile(r"\d[\d,]*(?:\.\d+)?")
HANGUL=re.compile(r"[가-힣]")
# 배너 요약은 명사구다. 말줄임표와 줄바꿈, 문장으로 끝나는 문구는 받지 않는다.
BANNER_REJECT=re.compile(r"…|\.{2,}|\n")
SENTENCE_END=re.compile(r"(?:니다|세요|[하이되]다)$|[.!?。]$")
NOUN_YO=re.compile(r"[수필개중주소강동]요$")

def sentence_ending(text):
    """~습니다, ~했다, ~한다, ~해요 같은 문장형 어미나 마침표로 끝나면 True. '수요' 같은 명사는 통과한다."""
    text=text.rstrip()
    if SENTENCE_END.search(text):return True
    if text.endswith('요'):return not NOUN_YO.search(text)
    if text.endswith('다') and len(text)>1 and '가'<=text[-2]<='힣':
        return (ord(text[-2])-0xAC00)%28 in (4,20)  # 받침 ㄴ·ㅆ: 한다·된다, 했다·늘었다
    return False

def numbers(text):
    out=set()
    for n in NUMBERS.findall(text):
        try:out.add(Decimal(n.replace(',','')).normalize())
        except InvalidOperation:pass
    return out
def compact(text):return re.sub(r"\s+","",text).casefold()

# 등락률은 배너 바로 위에 따로 나온다. 본 종목·국내 지수·업종의 주가 움직임만 되풀이하는 문구는 요약으로 받지 않는다.
MOVE=r"(?:강세|약세|급등|급락|반등|상승|하락)"
TREND=re.compile(r"상승세|하락세|오름세|내림세|주가\s*[\d.,%]*\s*"+MOVE+r"|(?:코스피|코스닥|증시|지수|종목|\S*[주株])\s*(?:동반\s*|일제히\s*)?"+MOVE)
ABROAD=re.compile(r"미국|美|뉴욕|나스닥|월가|해외|글로벌|중국|일본|유럽")

def restates_move(value,name):
    """등락률만 봐도 아는 말이면 True. 해외 시장의 움직임은 배경 사건으로 보고 통과시킨다."""
    if re.search(re.escape(name)+r"\s*(?:주가)?[\s\d.,%·]*"+MOVE,value):return True
    return bool(TREND.search(value)) and not ABROAD.search(value)

# 카드 문구(배너 요약, 관계)에는 화살표를 쓰지 않는다.
ARROW=re.compile(r"\s*(?:→|->)\s*")
WORD=re.compile(r"[가-힣A-Za-z]{2,}")
# 어느 기사에나 나오는 시장 말은 근거 확인에 쓰지 않는다.
TONES=("호재","악재","엇갈림")
GENERIC={"급등","급락","상승","하락","강세","약세","반등","코스피","코스닥","증시","주가","국내","시장","동반","매수","매도"}
# 관계 문구처럼 주가 말을 아예 쓰지 않는 자리에서 본다.
PRICE_TALK=re.compile(MOVE+r"|상승세|하락세|코스피|코스닥|증시|지수|주가|매수세|매도세|사이드카")
# 등락률이 바로 위에 나오므로 배너에 주가나 지수 움직임을 적으면 당연한 말이 된다.
# 상승·하락·반등은 사업 지표를 말할 때만 받는다('영업이익 상승', '실적 반등'). 그 밖에는 주가 이야기로 본다.
PRICE_WORD=re.compile(r"강세|약세|급등|급락|상승세|하락세|코스피|코스닥|증시|지수|주가|매수세|매도세|사이드카")
SOFT_MOVE=re.compile(r"상승|하락|반등")
BUSINESS_MOVE=re.compile(r"(?:실적|매출|이익|영업익|수익성?|마진|점유율|가격|단가|판가|원가|비용|금리|환율|유가|수요|출하량?|판매량?|수출|생산량?|가동률|수주|재고|배당|등급)\s*(?:상승|하락|반등)")
def price_talk(part):
    """주가·지수 이야기면 True."""
    return bool(PRICE_WORD.search(part) or SOFT_MOVE.search(BUSINESS_MOVE.sub(' ',part)))

# 지수·등락만 말하는 말. 등락 사유(title)는 이 말들 말고 무슨 일이 있었는지를 담아야 한다.
MARKET_ONLY={"급반등","급상승","급하락","상승세","하락세","출발","마감","장중","일제히","사이드카","외국인","기관","개인","순매수","순매도","훈풍","한파","회복","반락","조정","주춤","약진","만회","랠리","폭등","폭락","상한가","하한가","신고가","최고가","신저가","최저가","매수세","매도세","유입","유출","발동"}

PARTICLES=('에서','으로','에','의','로','와','과','도','은','는','을','를','이','가','속')
def stem(word):
    """'훈풍에'→'훈풍'. 남는 말이 두 글자 이상일 때만 조사를 뗀다."""
    for p in PARTICLES:
        if word.endswith(p) and len(word)-len(p)>=2:return word[:-len(p)]
    return word
def names_an_event(value,name):
    """'코스피 4% 급등'처럼 시장·등락 말만 있으면 False. '美 반도체 급등에 소부장 강세'처럼 무슨 일인지 있으면 True."""
    words={stem(w) for w in WORD.findall(value)}
    return any(w not in GENERIC and w not in MARKET_ONLY and w!=name for w in words)

# 앞의 여섯은 본 종목과의 관계, 제조사·업체는 같은 업종에서 다른 제품을 만드는 회사다.
ROLES={"경쟁사","고객사","공급사","협력사","계열사","동종업체","제조사","업체"}
SECTOR_OK={"반도체","장비","소재","부품","같은","업종","동종","제조","배터리","자동차","인터넷","플랫폼","바이오","조선","화학","철강","금융","게임","통신","전력","방산"}
def relation_text(value,names,all_titles):
    """관련 종목이 본 종목과 어떤 관계인지('HBM 장비 경쟁사'). 끝말은 관계, 앞말은 입력 기사나 업종 이름에 있는 말만 남긴다.
    '반도체 레이저 장비 업체'처럼 확인 안 된 말이 섞이면 그 말만 빼고('반도체 장비 업체'), 앞말이 하나도 안 남으면 버린다."""
    if not isinstance(value,str):return None
    value=' '.join(value.split())
    if not 2<=len(value)<=14 or not HANGUL.search(value) or NUMBERS.search(value) or ARROW.search(value):return None
    if FORBIDDEN.search(value) or BANNER_REJECT.search(value) or PRICE_TALK.search(value) or sentence_ending(value):return None
    if any(compact(n) in compact(value) for n in names):return None
    words=WORD.findall(value)
    if not words or not compact(value).endswith(words[-1].casefold()) or words[-1] not in ROLES:return None
    text=compact(' '.join(all_titles))
    known=[w for w in words[:-1] if compact(stem(w)) in text or stem(w) in SECTOR_OK]
    if len(known)<len(words)-1:
        return ' '.join(known+[words[-1]]) if known else None
    return value
def banner_text(value,name,own_titles):
    """카드에 보이는 한 줄 요약. 규칙에 맞으면 (다듬은 명사구, None)을, 아니면 (None, 버린 이유)를 돌려준다."""
    value=' '.join(value.split())
    if not 6<=len(value)<=28 or not HANGUL.search(value) or BANNER_REJECT.search(value) or ARROW.search(value) or sentence_ending(value):return None,'phrase'
    if FORBIDDEN.search(value):return None,'forbidden'
    # 등락률만 봐도 아는 말은 요약이 아니다. 해외 시장 움직임은 배경 사건으로 보고 통과시킨다.
    if restates_move(value,name) or (price_talk(value) and not ABROAD.search(value)) or not names_an_event(value,name):return None,'price_talk'
    # 종목 이름과 시장 말을 뺀 말이 그 종목 기사 제목에 하나는 있어야 한다.
    text=compact(' '.join(own_titles))
    if not any(compact(w) in text for w in {stem(w) for w in WORD.findall(value)}-GENERIC-{name}):return None,'ungrounded'
    return value,None

# 상세 제목은 일어난 일만 적는다. 기사나 보도 자체를 설명하는 말이 들어가면 받지 않는다.
META=re.compile(r"기사|보도|뉴스|제목|담겼|담긴|언급")
def detail_text(value,summary,name,cited,all_titles,notes=()):
    """상세 화면 제목. 배너와 같은 소식을 기사 제목과 앞부분(notes)으로 ~습니다 체 한두 문장으로 풀어 쓴 것이면 (문장, None)을, 아니면 (None, 버린 이유)를 돌려준다."""
    if not isinstance(value,str) or not value.strip():return None,'none'
    value=' '.join(value.split())
    if not 20<=len(value)<=140 or not HANGUL.search(value) or BANNER_REJECT.search(value) or ARROW.search(value):return None,'phrase'
    # 카드 배너와 달리 문장이어야 하고, 세 문장 이상은 받지 않는다.
    if not sentence_ending(value.rstrip('.!?。 ')) or len(re.findall(r"다[.!?。]\s*\S",value))>1:return None,'sentence'
    if FORBIDDEN.search(value):return None,'forbidden'
    if META.search(value):return None,'meta'
    # 등락률은 화면에 따로 나온다. 해외 시장 움직임을 사건으로 쓴 문장만 주가 말을 담을 수 있다.
    if PRICE_WORD.search(value) and not ABROAD.search(value):return None,'price_talk'
    # 종목 이름과 시장 말을 뺀 말이 인용한 기사 제목·앞부분에 둘 이상 있어야 하고, 숫자는 거기 있는 값만 쓴다.
    words={stem(w) for w in WORD.findall(value)}-GENERIC-{name}
    sources=[*cited,*notes];text=compact(' '.join(sources))
    if sum(compact(w) in text for w in words)<2:return None,'ungrounded'
    if not numbers(value)<=set().union(*(numbers(t) for t in sources)):return None,'numbers'
    # 기사 제목이나 앞부분 문장을 그대로 옮긴 것은 받지 않는다.
    body=compact(value.rstrip('.!?。 '))
    if body in {compact(t) for t in all_titles} or any(body in compact(n) for n in notes):return None,'copied'
    # 배너와 겹치는 말이 하나도 없으면 다른 소식을 쓴 것으로 본다.
    if not words&{stem(w) for w in WORD.findall(summary)}:return None,'unrelated'
    return value,None

def opposes_move(title,title_ids,banner,banner_ids,tone,rate):
    """등락 사유가 배너와 같은 소식인데 그 소식의 성격이 등락과 반대면(상승에 악재, 하락에 호재) True."""
    same=bool(set(title_ids)&set(banner_ids)) or (bool(banner) and compact(title)==compact(banner))
    return same and ((tone=='악재' and rate>0) or (tone=='호재' and rate<0))

def sanitize_stock_output(data,request):
    codes=[request.stock.code]+[s.code for s in request.related]
    def text(value,ids,allowed,limit):
        return (isinstance(value,str) and bool(value.strip()) and len(value)<=limit
            and not FORBIDDEN.search(value) and isinstance(ids,list) and 1<=len(ids)<=3
            and all(isinstance(i,str) and i in allowed for i in ids) and len(set(ids))==len(ids))
    market={h.id for h in request.market_headlines}
    move_market={h.id for h in request.market_headlines if h.in_move_window}
    own={h.id for h in request.stock.headlines}
    out=dict(title=None,title_ids=[],recent_issues=[],positive_factors=[],risk_factors=[],related=[])
    # AI 서버가 응답 필드를 먼저 늘려도 중계가 깨지지 않게, 필수 필드만 확인하고 모르는 필드는 무시한다.
    if not isinstance(data,dict) or not set(out)<=set(data) or any(not isinstance(data[f],list) for f in ('title_ids','recent_issues','positive_factors','risk_factors','related')):
        raise ValueError('Invalid stock response envelope')
    def title(row,stock,key):
        ids=row.get(key)
        allowed=move_market|{h.id for h in stock.headlines if h.in_move_window}
        if (stock.explain_move and abs(stock.change_rate)>=1 and text(row.get('title'),ids,allowed,20)
            and names_an_event(row['title'],stock.name)):return row['title'],ids
        return None,[]
    out['title'],out['title_ids']=title(data,request.stock,'title_ids')
    titles={h.id:h.title for s in [request.stock]+request.related for h in s.headlines}
    notes={h.id:h.summary for h in request.stock.headlines if getattr(h,'summary',None)}
    titles.update({h.id:h.title for h in request.market_headlines})
    def checked(value,ids,stock):
        if not isinstance(value,str) or not value.strip():return None,[],'none'
        if not (isinstance(ids,list) and all(isinstance(i,str) for i in ids) and len(set(ids))==len(ids)):return None,[],'ids'
        # 등락 기간과 무관하게 그 종목 자신의 기사만 근거로 삼는다. 시장·다른 종목 기사를 함께 적었으면 자기 기사만 남긴다.
        mine={h.id for h in stock.headlines};ids=[i for i in ids if i in mine]
        if not 1<=len(ids)<=3:return None,[],'ids'
        value,why=banner_text(value,stock.name,[h.title for h in stock.headlines])
        if value is None:return None,[],why
        # 숫자는 인용한 제목에 있는 값만, 문구는 기사 제목을 그대로 옮기지 않은 것만 허용한다.
        if not numbers(value)<=set().union(*(numbers(titles[i]) for i in ids)):return None,[],'numbers'
        if compact(value) in {compact(t) for t in titles.values()}:return None,[],'copied'
        return value,ids,'kept'
    def banner(value,ids,stock=request.stock):
        value,ids,why=checked(value,ids,stock)
        # 배너가 비었을 때 모델이 안 쓴 것(none)인지 어느 규칙에서 버려졌는지 남긴다.
        logger.info('stock_banner code=%s result=%s',stock.code,why)
        return value,ids
    # 이전 버전 AI 서버 응답에는 이 필드가 없고, 그때는 요약 없음으로 둔다.
    out['banner_summary'],out['banner_ids']=banner(data.get('banner_summary'),data.get('banner_ids'))
    def detail(value):
        # 상세 화면 제목은 카드 배너와 같은 기사로 같은 소식을 한 문장으로 풀어 쓴 것이다. 배너가 없으면 두지 않는다.
        if not out['banner_summary']:return None
        value,why=detail_text(value,out['banner_summary'],request.stock.name,[titles[i] for i in out['banner_ids']],titles.values(),[notes[i] for i in out['banner_ids'] if i in notes])
        logger.info('stock_banner_detail code=%s result=%s',request.stock.code,why or 'kept')
        return value
    out['banner_detail']=detail(data.get('banner_detail'))
    # 소식의 성격은 배너가 있을 때만 둔다. 주가 방향과 반대일 수 있어 화면이 따로 표시한다.
    tone=data.get('banner_tone')
    out['banner_tone']=tone if out['banner_summary'] and tone in TONES else None
    # 배너와 같은 소식을 등락 사유로 썼는데 그 소식이 등락과 반대 성격이면 사유로 맞지 않아 비운다.
    if out['title'] and opposes_move(out['title'],out['title_ids'],out['banner_summary'],out['banner_ids'],out['banner_tone'],request.stock.change_rate):
        out['title'],out['title_ids']=None,[]
    seen=set()
    for field in ('recent_issues','positive_factors','risk_factors'):
        rows=data.get(field,[])
        if not isinstance(rows,list):continue
        for row in rows:
            if not isinstance(row,dict):continue
            if text(row.get('text'),row.get('ids'),own|market if field=='recent_issues' else own,120) and row['text'] not in seen:
                out[field].append({'text':row['text'],'ids':row['ids']});seen.add(row['text'])
            if len(out[field])==3:break
    rows=data.get('related',[])
    if not isinstance(rows,list):rows=[]
    for stock in request.related:
        matches=[row for row in rows if isinstance(row,dict) and row.get('code')==stock.code]
        row=matches[0] if len(matches)==1 else {}
        value,ids=title(row,stock,'ids') if row else (None,[])
        # 관련 종목 카드도 같은 규칙의 배너를 쓴다. 근거는 그 종목 자신의 기사다.
        summary,banner_ids=banner(row.get('banner_summary'),row.get('banner_ids'),stock)
        tone=row.get('banner_tone') if summary and row.get('banner_tone') in TONES else None
        if value and opposes_move(value,ids,summary,banner_ids,tone,stock.change_rate):value,ids=None,[]
        # 소식이 없어도 카드가 '왜 함께 보는 종목인지'를 보여 줄 수 있게 관계를 따로 둔다.
        relation=relation_text(row.get('relation'),[request.stock.name,stock.name],titles.values())
        out['related'].append({'code':stock.code,'title':value,'ids':ids,'banner_summary':summary,'banner_ids':banner_ids,'banner_tone':tone,'relation':relation})
    accepted=bool(out['title'] or out['banner_summary'] or any(out[f] for f in ('recent_issues','positive_factors','risk_factors')) or any(x['title'] for x in out['related']))
    rejected=any(data.get(k) and not out.get(k) for k in ('title','banner_summary','recent_issues','positive_factors','risk_factors'))
    logger.info('stock_diagnostic code=%s result=%s',codes[0], 'accepted' if accepted else 'validation_failed:citations_or_text' if rejected else 'ai_abstained')
    return out
