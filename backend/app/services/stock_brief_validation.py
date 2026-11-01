import logging
import re
logger=logging.getLogger(__name__)
FORBIDDEN=re.compile(r"전망|기대|예상|예측|목표가|추천|매수하세요|매도하세요|사세요|파세요|할\s*것|때문에|영향으로|탓에")

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
    if not isinstance(data,dict) or set(data)!=set(out) or any(not isinstance(data[f],list) for f in ('title_ids','recent_issues','positive_factors','risk_factors','related')):
        raise ValueError('Invalid stock response envelope')
    def title(row,stock,key):
        ids=row.get(key)
        allowed=move_market|{h.id for h in stock.headlines if h.in_move_window}
        if stock.explain_move and abs(stock.change_rate)>=1 and text(row.get('title'),ids,allowed,20):return row['title'],ids
        return None,[]
    out['title'],out['title_ids']=title(data,request.stock,'title_ids')
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
        value,ids=title(matches[0],stock,'ids') if len(matches)==1 else (None,[])
        out['related'].append({'code':stock.code,'title':value,'ids':ids})
    accepted=bool(out['title'] or any(out[f] for f in ('recent_issues','positive_factors','risk_factors')) or any(x['title'] for x in out['related']))
    rejected=any(data.get(k) and not out.get(k) for k in ('title','recent_issues','positive_factors','risk_factors'))
    logger.info('stock_diagnostic code=%s result=%s',codes[0], 'accepted' if accepted else 'validation_failed:citations_or_text' if rejected else 'ai_abstained')
    return out
