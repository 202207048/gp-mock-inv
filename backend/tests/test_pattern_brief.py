import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace, ModuleType
from pathlib import Path
from unittest.mock import patch,AsyncMock
from uuid import UUID,uuid4
import sys,importlib,json
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base,get_db
from app.models import User,Account,Order,Portfolio,ItemMaster
from app.utils.deps import get_current_user
from app.routers import ai as routes
from app.services.pattern_brief_service import build_pattern_facts,rounded
from app.services import pattern_brief_client as bridge
from app.services import kis_service,news_service
from app.config import settings
from pydantic import SecretStr

RealClient=httpx.AsyncClient
BODY={'headline':'체결 기록을 돌아봅니다.','observed_patterns':[
 {'title':'매수','evidence':'매수 3건입니다.'},{'title':'매도','evidence':'매도 3건입니다.'}],
 'style_comparison':None,
 'limitations':[],'disclaimer':'모의투자 연습용 설명이며 투자 권유가 아닙니다.'}


class PatternTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine);self.addCleanup(self.engine.dispose)
        self.Session=sessionmaker(bind=self.engine)
        with self.Session() as db:
            for i in (1,2):
                db.add(User(user_id=i,login_id=str(i),passwd='fixture',email=f'{i}@example.invalid',user_name='fixture'))
                db.add(Account(account_id=i,user_id=i))
            for c in ('A','B'): db.add(ItemMaster(symbol_code=c,name=c,market_type='국내주식'))
            for i in range(6):
                db.add(Order(account_id=1,symbol_code='A' if i in (0,2,3,5) else 'B',
                  order_type='매수' if i in (0,1,5) else '매도',quantity=2 if i==0 else 1,
                  price=90 if i==2 else 110 if i==3 else 100,
                  sale_cost_basis=100 if i in (2,3) else None,
                  status='체결',price_type=['시장가','지정가','기타'][i%3],
                  funding_type='미수' if i==1 else '현금',
                  created_at=datetime(2026,10,1)+timedelta(seconds=[0,1799,1800,3600,3600,3600][i]),
                  filled_at=datetime(2026,10,1)+timedelta(days=i) if i<2 else None))
            for status in ('대기','취소','거부'):
                db.add(Order(account_id=1,symbol_code='A',order_type='매수',quantity=999,price=999,status=status))
            db.add(Portfolio(account_id=1,symbol_code='A',avg_price=100,hold_quantity=2))
            db.add(Portfolio(account_id=1,symbol_code='B',avg_price=100,hold_quantity=0));db.commit()
        app=FastAPI();app.include_router(routes.router,prefix='/api/ai')
        def session():
            with self.Session() as db: yield db
        app.dependency_overrides[get_db]=session
        app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(user_id=1)
        self.app=app;self.client=TestClient(app,raise_server_exceptions=False);self.addCleanup(self.client.close)
        self.patches=[patch.object(settings,'AI_SERVER_URL','https://ai.test'),
                      patch.object(settings,'AI_SERVICE_TOKEN',SecretStr('fixture-service')),
                      patch.object(kis_service,'get_current_price',AsyncMock(side_effect=AssertionError('KIS forbidden'))),
                      patch.object(news_service,'get_news_by_symbol',AsyncMock(side_effect=AssertionError('news forbidden')))]
        for p in self.patches: p.start();self.addCleanup(p.stop)

    def facts(self):
        with self.Session() as db: return build_pattern_facts(db,1,1)

    def test_exact_facts(self):
        f=self.facts()
        self.assertEqual(f.counts.model_dump(),dict(buy_filled=3,sell_filled=3,symbols_traded=2))
        self.assertEqual(f.period.days,1)
        self.assertEqual(f.concentration.top_symbol_buy_share_pct,75)
        self.assertEqual([x.name for x in f.concentration.top_symbols],['A','B'])
        self.assertEqual([x.buy_share_pct for x in f.concentration.top_symbols],[75,25])
        self.assertEqual(f.realized.model_dump(),dict(sells_with_basis=2,wins=1,losses=1,breakeven=0,unknown_basis=1,avg_realized_return_pct=0))
        self.assertEqual(f.order_style.market_ratio_pct,Decimal('33.33'))
        self.assertEqual(f.order_style.limit_ratio_pct,Decimal('33.33'))
        self.assertEqual(f.order_style.misu_count,1)
        self.assertEqual(f.timing.opening_30min_ratio_pct,Decimal('33.33'))
        self.assertEqual(f.holdings_count,1);self.assertIsNone(f.investment_style)
        self.assertEqual(rounded(Decimal('-0.00001')).as_tuple().sign,0)

    def test_null_period_and_no_basis(self):
        with self.Session() as db:
            db.query(Order).update({Order.filled_at:None,Order.sale_cost_basis:None});db.commit()
        f=self.facts();self.assertIsNone(f.period.first_filled_at);self.assertIsNone(f.period.last_filled_at)
        self.assertEqual(f.period.days,0);self.assertIsNone(f.realized.avg_realized_return_pct)

    def test_owner_rejected_on_both_routes(self):
        self.assertEqual(self.client.get('/api/ai/pattern/facts?account_id=2').status_code,404)
        self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':2}).status_code,404)

    def test_auth_required(self):
        def unauthorized():
            from fastapi import HTTPException
            raise HTTPException(401)
        self.app.dependency_overrides[get_current_user]=unauthorized
        self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1}).status_code,401)

    def test_insufficient_no_generation(self):
        with self.Session() as db:
            rows=db.query(Order).filter(Order.status=='체결').order_by(Order.order_id).all()
            for o in rows[2:]: db.delete(o)
            db.commit()
        with patch.object(routes,'request_pattern_brief',AsyncMock()) as call:
            result=self.client.post('/api/ai/pattern',json={'account_id':1})
        self.assertEqual(result.status_code,200);self.assertEqual(result.json(),{'status':'insufficient','filled_count':2})
        call.assert_not_called()

    def transport(self,handler):
        return patch.object(bridge.httpx,'AsyncClient',side_effect=lambda **kw:RealClient(transport=httpx.MockTransport(handler),**kw))

    def test_success_exact_wire_contract_one_request_and_private_metadata_removed(self):
        seen=[]
        package=ModuleType('_pattern_contract');package.__path__=[str(Path(__file__).resolve().parents[2]/'ai_server/app')]
        sys.modules['_pattern_contract']=package
        contract=importlib.import_module('_pattern_contract.pattern_schemas')
        def handler(req):
            seen.append(req)
            self.assertEqual(req.headers['Authorization'],'Bearer fixture-service')
            data=json.loads(req.content);contract.PatternBriefRequest.model_validate(data)
            self.assertEqual(data['profile'],'luna');self.assertNotIn('account_id',data['facts'])
            self.assertEqual(req.url.path,'/brief/pattern')
            return httpx.Response(200,json=BODY|{'provider':'openai','model':'gpt-6-luna','ai':{
                'provider':'openai','model':'gpt-6-luna','profile_id':'luna','profile_revision':1,
                'trace_id':req.headers['X-Trace-ID'],'operation_id':str(uuid4()),'usage':{},'latency_ms':1}})
        with self.transport(handler): result=self.client.post('/api/ai/pattern',json={'account_id':1})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(len(seen),1)
        self.assertEqual(set(result.json()),set(BODY)|{'style_tags','data_confidence','filled_count','investment_style_description'})
        self.assertEqual(result.json()['style_tags'],self.facts().model_dump(mode='json')['style_tags']);self.assertIn('X-Trace-ID',result.headers)
        kis_service.get_current_price.assert_not_called();news_service.get_news_by_symbol.assert_not_called()

    def test_facts_no_generation_or_external(self):
        with patch.object(routes,'request_pattern_brief',AsyncMock()) as call:
            result=self.client.get('/api/ai/pattern/facts?account_id=1')
        self.assertEqual(result.status_code,200);call.assert_not_called()
        kis_service.get_current_price.assert_not_called();news_service.get_news_by_symbol.assert_not_called()

    def test_upstream_errors_and_timeout(self):
        for code,expected in ((401,502),(403,502),(422,502),(500,502),(503,503),(200,502)):
            with self.subTest(code=code),self.transport(lambda req:httpx.Response(code,json={})):
                self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1}).status_code,expected)
        def timeout(req): raise httpx.ReadTimeout('fixture',request=req)
        with self.transport(timeout):
            self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1}).status_code,502)

    def test_missing_service_token_no_http(self):
        with patch.object(settings,'AI_SERVICE_TOKEN',SecretStr('')),patch.object(bridge.httpx,'AsyncClient') as call:
            result=self.client.post('/api/ai/pattern',json={'account_id':1})
        self.assertEqual(result.status_code,503);call.assert_not_called()

    def test_profile_validation_before_http(self):
        self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1,'profile':'unknown'}).status_code,422)


    def test_negative_average_and_style(self):
        with self.Session() as db:
            db.query(Order).filter(Order.order_type=='매도',Order.price==90).update({Order.price:80})
            db.query(User).filter(User.user_id==1).update({User.investment_style:'안정추구'})
            db.commit()
        f=self.facts()
        self.assertEqual(f.realized.avg_realized_return_pct,Decimal('-5.00'))
        self.assertEqual(f.investment_style,'안정추구')

    def test_no_fills_keeps_unknown_return_and_zero_ratios(self):
        with self.Session() as db:
            db.query(Order).filter(Order.status=='체결').delete();db.commit()
        f=self.facts();self.assertEqual(f.counts.buy_filled,0)
        self.assertEqual(f.order_style.market_ratio_pct,0)
        self.assertEqual(f.concentration.top_symbols,[])
        self.assertIsNone(f.realized.avg_realized_return_pct)
        self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1}).json(),
                         {'status':'insufficient','filled_count':0})

    def test_bad_url_and_connect_error_503(self):
        with patch.object(settings,'AI_SERVER_URL','bad://invalid'):
            self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1}).status_code,503)
        def fail(req): raise httpx.ConnectError('fixture',request=req)
        with self.transport(fail):
            self.assertEqual(self.client.post('/api/ai/pattern',json={'account_id':1}).status_code,503)


    def test_fifo_cases(self):
        from app.services.pattern_brief_service import holding_duration
        def order(side,q,day,symbol='A',hour=0):
            return SimpleNamespace(order_type=side,quantity=q,symbol_code=symbol,
                                   filled_at=datetime(2026,10,day,hour) if day else None)
        cases=[
          ([order('매수',5,1),order('매도',2,3)],(2,0,'2.00','0')),
          ([order('매수',2,1),order('매수',3,3),order('매도',4,4)],(4,0,'2.00','0')),
          ([order('매수',1,1),order('매도',3,3,'B')],(0,3,None,None)),
          ([order('매수',2,1),order('매도',3,1)],(2,1,'0','100')),
          ([order('매수',2,None),order('매도',1,2),order('매도',10,None)],(0,1,None,None)),
        ]
        for orders,expected in cases:
            with self.subTest(expected=expected):
                f=holding_duration(list(reversed(orders)))
                self.assertEqual((f['matched_sell_quantity'],f['unmatched_sell_quantity']),expected[:2])
                self.assertEqual(f['avg_holding_days'],Decimal(expected[2]) if expected[2] else None)
                self.assertEqual(f['same_day_ratio_pct'],Decimal(expected[3]) if expected[3] else None)
        buy=order('매수',1,1, hour=14);buy.filled_at=buy.filled_at.replace(minute=59)
        sell=order('매도',1,1,hour=15)
        self.assertEqual(holding_duration([sell,buy])['avg_holding_days'],1)

    def test_weighted_duration_and_independent_symbols(self):
        from app.services.pattern_brief_service import holding_duration
        def row(side,q,day,code='A'):
            return SimpleNamespace(order_type=side,quantity=q,symbol_code=code,filled_at=datetime(2026,10,day))
        f=holding_duration([row('매수',5,1),row('매수',5,3),row('매도',8,4),row('매도',5,5),
                            row('매수',1,1,'B'),row('매도',1,1,'B')])
        self.assertEqual(f,dict(matched_sell_orders=3,matched_sell_quantity=11,unmatched_sell_quantity=3,
                              avg_holding_days=Decimal('2.00'),same_day_ratio_pct=Decimal('9.09')))

    def test_tag_thresholds_order_and_limit(self):
        from app.services.pattern_brief_service import determine_style_tags
        f=SimpleNamespace(holding=SimpleNamespace(matched_sell_orders=3,matched_sell_quantity=1,avg_holding_days=0),
          concentration=SimpleNamespace(top_symbol_buy_share_pct=60),counts=SimpleNamespace(buy_filled=3,sell_filled=2,symbols_traded=3),
          order_style=SimpleNamespace(market_ratio_pct=70,limit_ratio_pct=30,misu_count=1),
          timing=SimpleNamespace(opening_30min_ratio_pct=50))
        codes=lambda:[t['code'] for t in determine_style_tags(f)]
        self.assertEqual(codes(),['day_trading','concentrated','market_order_heavy','opening_heavy','uses_misu'])
        for days,expected in [('0','day_trading'),('0.01','short_term'),('6.99','short_term'),('7','short_term'),
                              ('7.01','swing'),('29.99','swing'),('30','swing'),('30.01','long_term')]:
            f.holding.avg_holding_days=Decimal(days);self.assertEqual(codes()[0],expected)
        f.holding.matched_sell_quantity=0;f.holding.matched_sell_orders=0;self.assertFalse(set(codes())&{'day_trading','short_term','swing','long_term'})
        for share,expected in [('59.99',None),('60','concentrated'),('60.01','concentrated'),
                              ('29.99','diversified'),('30',None),('30.01',None)]:
            f.concentration.top_symbol_buy_share_pct=Decimal(share)
            self.assertEqual(next((c for c in codes() if c in ('concentrated','diversified')),None),expected)
        f.concentration.top_symbol_buy_share_pct=50;f.counts.symbols_traded=2
        self.assertNotIn('concentrated',codes())
        f.concentration.top_symbol_buy_share_pct=10;f.counts.symbols_traded=2
        self.assertNotIn('diversified',codes())
        for field,other,tag in [('market_ratio_pct','limit_ratio_pct','market_order_heavy'),
                                ('limit_ratio_pct','market_ratio_pct','limit_order_heavy')]:
            setattr(f.order_style,other,0)
            for value in ('69.99','70','70.01'):
                setattr(f.order_style,field,Decimal(value));self.assertEqual(tag in codes(),Decimal(value)>=70)
        for value in ('49.99','50','50.01'):
            f.timing.opening_30min_ratio_pct=Decimal(value);self.assertEqual('opening_heavy' in codes(),Decimal(value)>=50)
        for value in (0,1,2):
            f.order_style.misu_count=value;self.assertEqual('uses_misu' in codes(),value>=1)

    def test_comparison_unknown_rejected_and_server_tags_cannot_override(self):
        from app.schemas.pattern_brief import StyleComparison
        from pydantic import ValidationError
        self.assertEqual(StyleComparison(matches=[],differs=[]).model_dump(),{'matches':[],'differs':[]})
        for style in (None,'표밖'):
            facts=self.facts().model_copy(update={'investment_style':style})
            trace=uuid4()
            data=BODY|{'style_comparison':{'matches':[{'code':'no_misu','text':'미수 0건이에요.'}],'differs':[]},'provider':'openai','model':'gpt-6-luna',
                      'ai':{'provider':'openai','model':'gpt-6-luna','profile_id':'luna','profile_revision':1,
                            'trace_id':str(trace),'operation_id':str(uuid4()),'usage':{},'latency_ms':0}}
            with self.assertRaises(bridge.PatternBriefUnavailable):bridge._parse_result(data,facts,'luna',trace)
        fake=SimpleNamespace(ai=None,brief=lambda:BODY|{'style_tags':[{'code':'uses_misu','label':'가짜'}]})
        with patch.object(routes,'request_pattern_brief',AsyncMock(return_value=fake)):
            response=self.client.post('/api/ai/pattern',json={'account_id':1})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['style_tags'],self.facts().model_dump(mode='json')['style_tags'])

    def test_comparison_directives_rejected_and_old_questions_removed(self):
        from app.schemas.pattern_brief import StyleComparison, PatternBrief
        from pydantic import ValidationError
        for field in ('matches','differs'):
            for text in ('분산하세요', '분산해야 해요', '다른 주문을 해 보세요'):
                with self.assertRaises(ValidationError):
                    StyleComparison.model_validate({'matches':[], 'differs':[], field:[{'code':'no_misu','text':text}]})
        with self.assertRaises(ValidationError):
            PatternBrief.model_validate(BODY | {'reflection_questions':['이전 필드']})

    def catalog_facts(self, **changes):
        values=dict(misu=0,total=10,orders=3,top='40',symbols=2,same=None,matched=0,days=None,market=40,limit=40,opening=30)
        values.update(changes)
        dec=lambda x:None if x is None else Decimal(str(x))
        return SimpleNamespace(investment_style='안정추구형',
            counts=SimpleNamespace(buy_filled=values['total'],sell_filled=0,symbols_traded=values['symbols']),
            concentration=SimpleNamespace(top_symbol_buy_share_pct=dec(values['top'])),
            order_style=SimpleNamespace(misu_count=values['misu'],market_ratio_pct=dec(values['market']),limit_ratio_pct=dec(values['limit'])),
            timing=SimpleNamespace(opening_30min_ratio_pct=dec(values['opening'])),
            holding=SimpleNamespace(matched_sell_orders=values['orders'],matched_sell_quantity=values['matched'],avg_holding_days=dec(values['days']),same_day_ratio_pct=dec(values['same'])))

    def test_catalog_ten_behavior_boundaries_and_strength(self):
        from app.services.pattern_brief_service import _rank_behaviors
        cases=[('uses_misu','misu',0,1,2,'40.00'),
               ('concentrated','top','59.99','60','60.01','60.00'),
               ('day_trading','same','49.99','50','50.01','50.00'),
               ('market_heavy','market','69.99','70','70.01','70.00'),
               ('opening_heavy','opening','49.99','50','50.01','50.00'),
               ('patient_hold','days','7.99','8','8.01','26.67'),
               ('limit_used','limit','49.99','50','50.01','50.00')]
        for code,field,below,edge,above,strength in cases:
            for value,expected in ((below,False),(edge,True),(above,True)):
                f=self.catalog_facts(**{'matched':1,'days':0,field:value})
                found={item['code']:item['strength'] for item in _rank_behaviors(f)}
                with self.subTest(code=code,value=value):
                    self.assertEqual(code in found,expected)
                    if value==edge:self.assertEqual(found[code],Decimal(strength))
        def found(**kw):return {x['code']:x['strength'] for x in _rank_behaviors(self.catalog_facts(**kw))}
        for count in (2,3,4,5,6):
            self.assertEqual('no_misu' in found(total=count),count>=3)
            self.assertEqual('spread_timing' in found(total=count,opening=0),count>=5)
        self.assertNotIn('no_misu',found(misu=1))
        self.assertEqual(found()['no_misu'],60)
        for top in ('29.99','30','30.01'):
            self.assertEqual('diversified' in found(top=top,symbols=3),Decimal(top)<30)
        self.assertNotIn('diversified',found(top=20,symbols=2))
        self.assertEqual(found(top='29.99',symbols=3)['diversified'],Decimal('70.01'))
        for opening in ('19.99','20','20.01'):
            self.assertEqual('spread_timing' in found(opening=opening),Decimal(opening)<20)
        self.assertEqual(found(opening='19.99')['spread_timing'],Decimal('80.01'))
        self.assertEqual(found(misu=2,total=3)['uses_misu'],Decimal('66.67'))
        self.assertEqual(found(misu=3,total=3)['uses_misu'],100)
        self.assertEqual(found(days=60,matched=1)['patient_hold'],100)
        self.assertNotIn('patient_hold',found(days=10,matched=0))
        self.assertNotIn('day_trading',found(same=None))

    def test_style_basis_five_styles_sort_tie_and_top_three(self):
        from app.services.pattern_brief_service import determine_style_comparison_basis,_rank_behaviors
        f=self.catalog_facts(misu=2,total=10,top=65,same=60,matched=1,days=9,market=75,opening=55)
        expected={
          '안정형':(['patient_hold'],['concentrated','day_trading','uses_misu']),
          '안정추구형':(['patient_hold'],['concentrated','day_trading','uses_misu']),
          '위험중립형':(['patient_hold'],['concentrated','day_trading','uses_misu']),
          '적극투자형':(['concentrated','day_trading','uses_misu'],[]),
          '공격투자형':(['concentrated','day_trading','uses_misu'],[])}
        for style,(matches,differs) in expected.items():
            f.investment_style=style;actual=determine_style_comparison_basis(f)
            self.assertEqual([x['code'] for x in actual['matches']],matches)
            self.assertEqual([x['code'] for x in actual['differs']],differs)
        for style in (None,'표밖'):
            f.investment_style=style;self.assertIsNone(determine_style_comparison_basis(f))
        tied=self.catalog_facts(misu=7,total=10,top=70,same=70,market=70,opening=70)
        self.assertEqual([x['code'] for x in _rank_behaviors(tied)],
                         ['uses_misu','concentrated','day_trading','market_heavy','opening_heavy'])
        negatives=self.catalog_facts(top=20,symbols=3,matched=1,days=30,limit=80,opening=10)
        self.assertEqual([x['code'] for x in _rank_behaviors(negatives)],
                         ['patient_hold','diversified','no_misu','spread_timing','limit_used'])
        self.assertEqual([x['code'] for x in determine_style_comparison_basis(negatives)['matches']],
                         ['patient_hold','diversified','no_misu'])

    def test_realized_breakeven_unknown_and_computed_basis(self):
        with self.Session() as db:
            db.query(Order).filter(Order.status=='체결',Order.price==110).update({Order.price:100})
            db.query(User).filter(User.user_id==1).update({User.investment_style:'안정추구형'})
            db.commit()
        f=self.facts()
        self.assertEqual((f.realized.breakeven,f.realized.unknown_basis),(1,1))
        self.assertEqual(f.style_comparison_basis.model_dump(),{'matches':[],'differs':[{'code':'concentrated','strength':Decimal('75.00')}]})

    def test_bridge_revalidates_basis_order_missing_extra_empty_and_null(self):
        from app.schemas.pattern_brief import StyleComparisonBasis
        base=self.facts();trace=uuid4()
        def wire(comparison):
            return BODY|{'style_comparison':comparison,'provider':'openai','model':'gpt-6-luna',
                 'ai':{'provider':'openai','model':'gpt-6-luna','profile_id':'luna','profile_revision':1,
                       'trace_id':str(trace),'operation_id':str(uuid4()),'usage':{},'latency_ms':0}}
        def comparison(codes):
            return {'matches':[],'differs':[{'code':c,'text':'안정형과 비교하면 미수 1건이었어요.'} for c in codes]}
        facts=base.model_copy(update={'investment_style':'안정형',
              'style_comparison_basis':StyleComparisonBasis(matches=[],differs=[{'code':'uses_misu','strength':60},{'code':'concentrated','strength':60}])})
        for codes in (['concentrated','uses_misu'],['uses_misu'],['uses_misu','concentrated','day_trading']):
            with self.assertRaises(bridge.PatternBriefUnavailable):bridge._parse_result(wire(comparison(codes)),facts,'luna',trace)
        valid=comparison(['uses_misu','concentrated'])
        self.assertEqual(bridge._parse_result(wire(valid),facts,'luna',trace).brief()['style_comparison'],valid)
        empty=base.model_copy(update={'investment_style':'위험중립형','style_comparison_basis':StyleComparisonBasis(matches=[],differs=[])})
        bridge._parse_result(wire(comparison([])),empty,'luna',trace)
        with self.assertRaises(bridge.PatternBriefUnavailable):bridge._parse_result(wire(valid),base,'luna',trace)

    def test_s7_strong_first_and_highlights_exclude_comparison(self):
        from app.services.pattern_brief_service import determine_style_comparison_basis,determine_pattern_highlights
        f=self.catalog_facts(misu=2,total=7,top=80,market='85.71',opening='57.14',matched=2,days='5.5',same=0)
        basis=determine_style_comparison_basis(f)
        self.assertEqual([x['code'] for x in basis['differs']],['concentrated'])
        self.assertEqual(basis['matches'],[])
        self.assertEqual([x['code'] for x in determine_pattern_highlights(f)],['uses_misu','market_heavy','opening_heavy'])
        f.investment_style=None
        self.assertIsNone(determine_style_comparison_basis(f))
        self.assertEqual([x['code'] for x in determine_pattern_highlights(f)],['concentrated','uses_misu','market_heavy'])
        f.investment_style='표밖'
        self.assertEqual([x['code'] for x in determine_pattern_highlights(f)],['concentrated','uses_misu','market_heavy'])

    def test_built_facts_highlights_are_disjoint_from_basis(self):
        with self.Session() as db:
            db.query(User).filter(User.user_id==1).update({User.investment_style:'안정추구형'});db.commit()
        f=self.facts()
        used={x.code for x in f.style_comparison_basis.matches+f.style_comparison_basis.differs}
        self.assertFalse(used & {x.code for x in f.pattern_highlights})
        self.assertEqual([x.code for x in f.pattern_highlights],['uses_misu'])

    def test_sample_minima_apply_to_catalog_and_tags(self):
        from app.services.pattern_brief_service import _rank_behaviors,determine_style_tags
        for total in (4,5):
            f=self.catalog_facts(total=total,market=75,limit=75,opening=60,top=80)
            codes={x['code'] for x in _rank_behaviors(f)};tags={x['code'] for x in determine_style_tags(f)}
            for code in ('market_heavy','limit_used','opening_heavy'):self.assertEqual(code in codes,total>=5)
            self.assertEqual('market_order_heavy' in tags,total>=5)
            self.assertEqual('opening_heavy' in tags,total>=5)
            f.timing.opening_30min_ratio_pct=Decimal(10)
            self.assertEqual('spread_timing' in {x['code'] for x in _rank_behaviors(f)},total>=5)
            f.order_style.market_ratio_pct=0
            self.assertEqual('limit_order_heavy' in {x['code'] for x in determine_style_tags(f)},total>=5)
        for buys in (2,3):
            f=self.catalog_facts(top=80);f.counts.buy_filled=buys
            self.assertEqual('concentrated' in {x['code'] for x in _rank_behaviors(f)},buys>=3)
            self.assertEqual('concentrated' in {x['code'] for x in determine_style_tags(f)},buys>=3)
            f.concentration.top_symbol_buy_share_pct=Decimal(20);f.counts.symbols_traded=3
            self.assertEqual('diversified' in {x['code'] for x in _rank_behaviors(f)},buys>=3)
            self.assertEqual('diversified' in {x['code'] for x in determine_style_tags(f)},buys>=3)
        for orders in (2,3):
            f=self.catalog_facts(orders=orders,matched=100,days=0,same=100)
            self.assertEqual('day_trading' in {x['code'] for x in _rank_behaviors(f)},orders>=3)
            self.assertEqual('day_trading' in {x['code'] for x in determine_style_tags(f)},orders>=3)
            f.holding.avg_holding_days=Decimal(10)
            self.assertEqual('patient_hold' in {x['code'] for x in _rank_behaviors(f)},orders>=3)
            self.assertEqual('swing' in {x['code'] for x in determine_style_tags(f)},orders>=3)
        f=self.catalog_facts(total=1,misu=1)
        self.assertIn('uses_misu',{x['code'] for x in _rank_behaviors(f)})
        self.assertIn('uses_misu',{x['code'] for x in determine_style_tags(f)})

    def test_matched_sell_order_count_is_not_lot_or_share_count(self):
        from app.services.pattern_brief_service import holding_duration
        def row(side,q,day):return SimpleNamespace(order_type=side,quantity=q,symbol_code='A',filled_at=datetime(2026,10,day) if day else None)
        f=holding_duration([row('매수',50,1),row('매수',50,2),row('매도',75,3),row('매도',40,4),
                            row('매도',1,5),row('매도',10,None)])
        self.assertEqual((f['matched_sell_orders'],f['matched_sell_quantity'],f['unmatched_sell_quantity']),(2,100,16))

    def test_confidence_boundaries_comparison_limit_and_public_fields(self):
        from app.services.pattern_brief_service import data_confidence,determine_style_comparison_basis
        for count,expected in ((3,'preliminary'),(9,'preliminary'),(10,'moderate'),(29,'moderate'),(30,'sufficient')):
            self.assertEqual(data_confidence(count),expected)
            f=self.catalog_facts(total=count,misu=1,top=80,same=70,matched=3,days=0,market=90,opening=80)
            self.assertEqual(len(determine_style_comparison_basis(f)['differs']),1 if count<10 else 3)
        fake=SimpleNamespace(ai=None,brief=lambda:BODY)
        with patch.object(routes,'request_pattern_brief',AsyncMock(return_value=fake)):
            body=self.client.post('/api/ai/pattern',json={'account_id':1}).json()
        self.assertEqual(body['filled_count'],6);self.assertEqual(body['data_confidence'],'preliminary')
        self.assertIsNone(body['investment_style_description'])

    def test_fixed_style_description_five_and_unknown(self):
        from app.schemas.pattern_brief import INVESTMENT_STYLE_DESCRIPTIONS
        for style in [*INVESTMENT_STYLE_DESCRIPTIONS,None,'표밖']:
            with self.Session() as db:
                db.query(User).filter(User.user_id==1).update({User.investment_style:style});db.commit()
            self.assertEqual(self.facts().investment_style_description,INVESTMENT_STYLE_DESCRIPTIONS.get(style))

    def test_fixed_notes_catalog_and_language(self):
        from typing import get_args
        import re
        from app.schemas.pattern_brief import COMPARISON_NOTES,ComparisonCode,COMPARISON_DIRECTIVES,PROHIBITED_LANGUAGE_PATTERNS
        self.assertEqual(set(COMPARISON_NOTES),set(get_args(ComparisonCode)))
        self.assertEqual(sum('option' in v for v in COMPARISON_NOTES.values()),5)
        for value in COMPARISON_NOTES.values():
            self.assertTrue(value['reason'])
            for text in value.values():
                self.assertFalse(any(re.search(p,text) for p in PROHIBITED_LANGUAGE_PATTERNS))
                self.assertFalse(any(p in text for p in COMPARISON_DIRECTIVES))

    def test_public_notes_direction_and_null(self):
        from app.schemas.pattern_brief import attach_comparison_notes,COMPARISON_NOTES
        self.assertIsNone(attach_comparison_notes(None))
        items=[{'code':code,'text':'관찰했어요.'} for code in COMPARISON_NOTES]
        result=attach_comparison_notes({'matches':items,'differs':items})
        self.assertTrue(all(x['note']['option'] is None for x in result['matches']))
        for item in result['differs']:
            self.assertEqual(item['note'],{'reason':COMPARISON_NOTES[item['code']]['reason'],'option':COMPARISON_NOTES[item['code']].get('option')})
        self.assertNotIn('note',items[0])
        self.assertIsNone(attach_comparison_notes({'matches':[],'differs':[{'code':'unknown','text':'관찰'}]})['differs'][0]['note'])

    def test_notes_are_added_only_to_public_route(self):
        from app.schemas.pattern_brief import COMPARISON_NOTES
        comparison={'matches':[{'code':'uses_misu','text':'미수 주문 1건이었어요.'}],
                    'differs':[{'code':'concentrated','text':'비중 75%였어요.'}]}
        fake=SimpleNamespace(ai=None,brief=lambda:BODY|{'style_comparison':comparison})
        with patch.object(routes,'request_pattern_brief',AsyncMock(return_value=fake)):
            response=self.client.post('/api/ai/pattern',json={'account_id':1})
        self.assertEqual(response.status_code,200,response.text)
        public=response.json()['style_comparison']
        self.assertIsNone(public['matches'][0]['note']['option'])
        self.assertEqual(public['differs'][0]['note']['option'],COMPARISON_NOTES['concentrated']['option'])
        self.assertNotIn('note',comparison['matches'][0])
        self.assertNotIn('note',self.client.get('/api/ai/pattern/facts?account_id=1').text)
