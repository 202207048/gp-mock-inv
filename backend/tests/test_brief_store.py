import asyncio
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import AsyncMock,Mock,patch
from sqlalchemy import create_engine,select,func,event,inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.models import AiBriefReport,AiBriefResult,User,Account
from app.services import brief_store as store
from app.services.stock_brief_service import StockBriefService
from app.schemas.stock_brief import StockPublicResponse
import test_stock_brief as stock
import test_portfolio_brief_integration as portfolio

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
  self.addCleanup(self.engine.dispose)
  Base.metadata.create_all(self.engine);self.Session=sessionmaker(bind=self.engine)
  patcher=patch.object(store,'SessionLocal',self.Session);patcher.start();self.addCleanup(patcher.stop)
  with self.Session() as db:
   db.add_all([User(user_id=i,login_id=str(i),passwd='unused',user_name='test',email=f'{i}@test.invalid') for i in (1,2)])
   db.flush();db.add_all([Account(account_id=i,user_id=i,balance=100,withdrawable_cash=100) for i in (1,2)]);db.commit()
 def service(self,**kwargs):
  return StockBriefService(quote=AsyncMock(side_effect=lambda c:stock.quote()),related=AsyncMock(return_value=[]),
   collect=AsyncMock(side_effect=lambda *a:(stock.articles(),{},{})),generate=AsyncMock(side_effect=lambda r:stock.generated()),**kwargs)
 def test_stock_restart_restores_and_overwrites_quote(self):
  first=self.service();original=asyncio.run(first.get('000001'))
  second=self.service();second.quote.return_value={**stock.quote(),'price':99};second.quote.side_effect=None
  restored=asyncio.run(second.get('000001'));second.generate.assert_not_awaited()
  self.assertEqual(restored.price,99);self.assertEqual(restored.title,original.title)
 def test_expired_stock_replaced_in_one_row(self):
  first=self.service();asyncio.run(first.get('000001'))
  with self.Session() as db:
   db.scalar(select(AiBriefResult)).created_at=datetime.now(timezone.utc)-timedelta(hours=4);db.commit()
  second=self.service();asyncio.run(second.get('000001'));second.generate.assert_awaited_once()
  with self.Session() as db:self.assertEqual(db.scalar(select(func.count()).select_from(AiBriefResult)),1)
 def test_restore_keeps_age_for_direction_change(self):
  for minutes,expected in ((29,0),(31,1)):
   service=self.service();asyncio.run(service.get('000001'))
   with self.Session() as db:
    db.scalar(select(AiBriefResult)).created_at=datetime.now(timezone.utc)-timedelta(minutes=minutes);db.commit()
   second=self.service();second.quote.side_effect=lambda c:{**stock.quote(),'change_rate':2}
   asyncio.run(second.get('000001'));self.assertEqual(second.generate.await_count,expected)
 def test_unsuccessful_results_not_persisted(self):
  for mode in ('failure','empty','abstained'):
   service=self.service()
   if mode=='failure':service.generate.side_effect=RuntimeError('fake')
   elif mode=='empty':service.collect.side_effect=None;service.collect.return_value=({},{},{})
   else:service.generate.side_effect=lambda r:{**stock.generated(),'title':None,'title_ids':[],'recent_issues':[],'banner_summary':None,'banner_ids':[],'banner_tone':None}
   self.assertFalse(asyncio.run(service.get('000001')).ai_available)
   self.assertIsNone(store.load_stock('000001'))
 def test_injected_load_and_save_failure_do_not_break_response(self):
  service=self.service(load=Mock(side_effect=RuntimeError('private')),save=Mock(side_effect=RuntimeError('private')))
  self.assertTrue(asyncio.run(service.get('000001')).ai_available)
 def test_store_errors_are_safe_and_rollback(self):
  with patch.object(store,'SessionLocal',side_effect=RuntimeError('private')):
   self.assertIsNone(store.load_stock('000001'));store.save_stock('000001',{})
  db=Mock();db.add.side_effect=RuntimeError('private');store.save_portfolio(db,1,1,'A',{});db.rollback.assert_called_once()
  db=Mock();db.scalars.side_effect=RuntimeError('private');self.assertEqual(store.list_portfolio(db,1,1,'A'),[])
 def test_portfolio_retention_and_scope(self):
  with self.Session() as db:
   for n in range(3):store.save_portfolio(db,1,1,'A',{'n':n})
   store.save_portfolio(db,1,1,'B',{'n':10});store.save_portfolio(db,2,2,'A',{'n':20})
   self.assertEqual(store.list_portfolio(db,1,1,'A'),[{'n':2},{'n':1}])
   self.assertEqual(store.list_portfolio(db,1,1,'B'),[{'n':10}])
   self.assertEqual(store.list_portfolio(db,2,2,'A'),[{'n':20}])
   self.assertEqual([p for _,p in store.list_history(db,1,1)],[{'n':10},{'n':2},{'n':1},{'n':0}])
 def test_three_day_cutoff_hides_then_prunes_older_rows(self):
  now=datetime(2026,11,2,1,0,tzinfo=timezone.utc)  # KST 11-02 10:00
  self.assertEqual(store.cutoff(now),datetime(2026,10,29,15,0,tzinfo=timezone.utc))  # KST 10-30 00:00
  with self.Session() as db:
   for n in range(2):store.save_portfolio(db,1,1,'A',{'n':n})
   rows=list(db.scalars(select(AiBriefResult).order_by(AiBriefResult.id)))
   rows[0].created_at=store.cutoff()-timedelta(seconds=1);rows[1].created_at=store.cutoff()+timedelta(seconds=1);db.commit()
   self.assertEqual([p for _,p in store.list_history(db,1,1)],[{'n':1}])
   store.save_portfolio(db,1,1,'B',{'n':2})
   self.assertEqual(db.scalar(select(func.count()).select_from(AiBriefResult)),2)
 def test_keep_limit_and_owned_delete(self):
  with patch.object(store,'KEEP_LIMIT',3),self.Session() as db:
   for n in range(5):store.save_portfolio(db,1,1,'A',{'n':n})
   history=store.list_history(db,1,1);self.assertEqual([p for _,p in history],[{'n':4},{'n':3},{'n':2}])
   self.assertFalse(store.delete_portfolio(db,2,2,history[0][0]))
   self.assertTrue(store.delete_portfolio(db,1,1,history[0][0]))
   self.assertEqual([p for _,p in store.list_history(db,1,1)],[{'n':3},{'n':2}])

class SavedApiTests(unittest.TestCase):
 setUp=portfolio.EndpointTests.setUp
 get=portfolio.EndpointTests.get
 def saved(self,account=1,symbol='SYN001'):
  return self.client.get(f'/api/ai/portfolio-brief/{symbol}/saved?account_id={account}')
 def test_success_saves_and_saved_is_offline(self):
  responses=[self.get().json() for _ in range(3)]
  self.generate.reset_mock();self.quote.reset_mock();self.news.reset_mock()
  response=self.saved();self.assertEqual(response.status_code,200,response.text)
  self.assertEqual(response.json()['items'],list(reversed(responses[-2:])))
  self.generate.assert_not_awaited();self.quote.assert_not_awaited();self.news.assert_not_awaited()
  with self.Session() as db:self.assertEqual(db.scalar(select(func.count()).select_from(AiBriefResult)),3)
 def test_failed_generation_not_saved(self):
  self.generate.side_effect=portfolio.service.PortfolioBriefUnavailable(502)
  self.assertEqual(self.get().status_code,502);self.assertEqual(self.saved().json(),{'items':[]})
 def test_saved_ownership_auth_no_holding_and_invalid_payload(self):
  self.assertEqual(self.saved(account=2).status_code,404)
  self.assertEqual(self.saved(symbol='NOT_HELD').json(),{'items':[]})
  with self.Session() as db:store.save_portfolio(db,1,1,'SYN001',{'invalid':True})
  self.assertEqual(self.saved().json(),{'items':[]})
  self.app.dependency_overrides.pop(portfolio.get_current_user)
  self.assertIn(self.saved().status_code,(401,403))
 def test_saved_retrieves_after_position_removed_and_does_not_mix_symbols(self):
  payload=self.get().json()
  with self.Session() as db:
   db.query(portfolio.Portfolio).delete();db.commit()
  self.assertEqual(self.saved().json()['items'],[payload])
  self.assertEqual(self.saved(symbol='OTHER').json(),{'items':[]})

class HistoryAndReportApiTests(unittest.TestCase):
 setUp=portfolio.EndpointTests.setUp
 get=portfolio.EndpointTests.get
 def history(self,account=1):
  return self.client.get(f'/api/ai/portfolio-briefs?account_id={account}')
 def report(self,**body):
  return self.client.post('/api/ai/brief-reports',json={'kind':'portfolio','symbol_code':'SYN001','account_id':1,'reason':'wrong_fact',**body})
 def test_history_lists_with_ids_and_delete_is_owner_only(self):
  first,second=self.get().json(),self.get().json()
  items=self.history().json()['items']
  self.assertEqual([item['generated_at'] for item in items],[second['generated_at'],first['generated_at']])
  self.assertTrue(all(isinstance(item['id'],int) for item in items))
  self.assertEqual(self.history(account=2).status_code,404)
  self.assertEqual(self.client.delete(f"/api/ai/portfolio-briefs/{items[0]['id']}?account_id=2").status_code,404)
  self.assertEqual(self.client.delete(f"/api/ai/portfolio-briefs/{items[0]['id']}?account_id=1").status_code,204)
  self.assertEqual(self.client.delete(f"/api/ai/portfolio-briefs/{items[0]['id']}?account_id=1").status_code,404)
  self.assertEqual([item['id'] for item in self.history().json()['items']],[items[1]['id']])
 def test_portfolio_report_stores_server_copy_once(self):
  brief=self.get().json()
  response=self.report(generated_at=brief['generated_at'],note='  숫자가 이상해요  ')
  self.assertEqual(response.status_code,201,response.text)
  with self.Session() as db:
   row=db.scalar(select(AiBriefReport))
   self.assertEqual((row.user_id,row.account_id,row.kind,row.symbol_code,row.reason,row.note),(1,1,'portfolio','SYN001','wrong_fact','숫자가 이상해요'))
   self.assertEqual(row.payload,brief)
  self.assertEqual(self.report(generated_at=brief['generated_at']).status_code,409)
  self.assertEqual(self.report(generated_at='2026-01-01T00:00:00+09:00').status_code,404)
  self.assertEqual(self.report(generated_at=brief['generated_at'],account_id=2).status_code,404)
  self.assertEqual(self.report(generated_at=brief['generated_at'],payload={'x':1}).status_code,422)
  self.assertEqual(self.report(generated_at=brief['generated_at'],reason='spam').status_code,422)
  self.assertEqual(self.report(generated_at=brief['generated_at'],note='가'*201).status_code,422)
 def test_stock_report_uses_current_shared_briefing_and_daily_limit(self):
  payload={'symbol_code':'000660','generated_at':'2026-11-02T12:40:00+09:00','title':'서버 원문'}
  with patch.object(store,'load_stock',return_value=(payload,datetime.now(timezone.utc))):
   ok=self.report(kind='stock',symbol_code='000660',account_id=None,generated_at=payload['generated_at'])
   self.assertEqual(ok.status_code,201,ok.text)
   self.assertEqual(self.report(kind='stock',symbol_code='000660',generated_at='2026-11-02T09:00:00+09:00').status_code,404)
   with patch('app.services.brief_report.DAILY_LIMIT',1):
    self.assertEqual(self.report(kind='stock',symbol_code='000660',generated_at=payload['generated_at'],reason='other').status_code,429)
  with self.Session() as db:
   row=db.scalar(select(AiBriefReport));self.assertEqual((row.kind,row.account_id,row.payload),('stock',None,payload))
 def test_report_requires_login(self):
  self.app.dependency_overrides.pop(portfolio.get_current_user)
  self.assertIn(self.report(generated_at='2026-11-02T12:40:00+09:00').status_code,(401,403))

class SaveFailureApiTests(unittest.TestCase):
 setUp=portfolio.EndpointTests.setUp
 get=portfolio.EndpointTests.get
 def test_save_commit_error_preserves_generated_response(self):
  with patch('app.services.brief_store.logger') as logger,patch('sqlalchemy.orm.Session.commit',side_effect=RuntimeError('private')):
   response=self.get()
  self.assertEqual(response.status_code,200,response.text)
  self.assertNotIn('private',response.text);logger.warning.assert_called_once()
  with self.Session() as db:self.assertEqual(db.scalar(select(func.count()).select_from(AiBriefResult)),0)

class MigrationTests(unittest.TestCase):
 def test_new_revision_upgrade_index_and_downgrade(self):
  from alembic.migration import MigrationContext
  from alembic.operations import Operations
  from alembic.script import ScriptDirectory
  from pathlib import Path
  scripts=ScriptDirectory(str(Path(__file__).resolve().parents[1]/'alembic'))
  revision=scripts.get_revision('j20261102_ai_brief_results').module
  self.assertEqual(revision.down_revision,'i20261028_sale_cost_basis')
  engine=create_engine('sqlite://');self.addCleanup(engine.dispose)
  with engine.begin() as conn:
   with Operations.context(MigrationContext.configure(conn)):
    revision.upgrade()
    self.assertIn('ai_brief_results',inspect(conn).get_table_names())
    self.assertEqual(inspect(conn).get_indexes('ai_brief_results')[0]['column_names'],['kind','symbol_code','user_id','account_id','created_at'])
    revision.downgrade();self.assertNotIn('ai_brief_results',inspect(conn).get_table_names())
 def test_report_revision_upgrade_and_downgrade(self):
  from alembic.migration import MigrationContext
  from alembic.operations import Operations
  from alembic.script import ScriptDirectory
  from pathlib import Path
  scripts=ScriptDirectory(str(Path(__file__).resolve().parents[1]/'alembic'))
  revision=scripts.get_revision('k20261102_ai_brief_reports').module
  self.assertEqual(revision.down_revision,'j20261102_ai_brief_results')
  engine=create_engine('sqlite://');self.addCleanup(engine.dispose)
  with engine.begin() as conn:
   conn.exec_driver_sql('create table users (user_id integer primary key)')
   conn.exec_driver_sql('create table account (account_id integer primary key)')
   with Operations.context(MigrationContext.configure(conn)):
    revision.upgrade()
    self.assertEqual(inspect(conn).get_indexes('ai_brief_reports')[0]['column_names'],['user_id','created_at'])
    revision.downgrade();self.assertNotIn('ai_brief_reports',inspect(conn).get_table_names())
