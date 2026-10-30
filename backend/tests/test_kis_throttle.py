import asyncio
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from app.services import kis_service as kis


class ThrottleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_token = kis.get_kis_token
        self.now = 0.0
        self.real_sleep = asyncio.sleep
        async def sleep(delay):
            self.now += delay
            await self.real_sleep(0)
        self.patches = [patch.object(kis, 'monotonic', lambda: self.now),
                        patch.object(kis.asyncio, 'sleep', sleep),
                        patch.object(kis.settings, 'KIS_MIN_INTERVAL_MS', 500),
                        patch.object(kis.settings, 'KIS_APP_KEY', 'fixture'),
                        patch.object(kis, 'get_kis_token', AsyncMock(return_value='fixture'))]
        for p in self.patches: p.start()
        self.starts = []
        self.codes = []
        self.failure = False
        outer = self
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, url, **kwargs):
                outer.starts.append(outer.now)
                if outer.failure: raise httpx.ConnectError('fixture')
                code = outer.codes.pop(0) if outer.codes else '0'
                return httpx.Response(200, json={'rt_cd': '0' if code=='0' else '1',
                    'msg_cd': code, 'output': {'stck_prpr': '123'}}, request=httpx.Request('GET',url))
            post = get
        self.client = Client()
        self.patches.append(patch.object(kis.httpx,'AsyncClient',Client))
        self.patches[-1].start()
    async def asyncTearDown(self):
        for p in reversed(self.patches): p.stop()

    async def test_distinct_symbols_are_spaced(self):
        result=await asyncio.gather(*(kis.get_current_price(str(i)) for i in range(30)))
        self.assertEqual(len(result),30)
        self.assertEqual(len(self.starts),30)
        self.assertTrue(all(b-a >= .5 for a,b in zip(self.starts,self.starts[1:])))

    async def test_same_symbol_shares_request_and_cached_values_are_isolated(self):
        values=await asyncio.gather(*(kis.get_current_price('A') for _ in range(10)))
        self.assertEqual(len(self.starts),1)
        values[0]['current_price']=999
        self.assertEqual((await kis.get_current_price('A'))['current_price'],123)
        self.assertEqual(values[1]['current_price'],123)

    async def test_ttl_expiry(self):
        await kis.get_current_price('A');self.now=9.99
        await kis.get_current_price('A');self.assertEqual(len(self.starts),1)
        self.now=10
        await kis.get_current_price('A');self.assertEqual(len(self.starts),2)

    async def test_failure_not_cached(self):
        self.failure=True
        for _ in range(2):
            with self.assertRaises(httpx.ConnectError): await kis.get_current_price('A')
        self.assertEqual(len(self.starts),2)
        self.failure=False
        await kis.get_current_price('A')
        self.assertEqual(len(self.starts),3)

    async def test_limit_retry_success(self):
        self.codes=['EGW00201','0']
        await kis.get_current_price('A')
        self.assertEqual(self.starts,[0,.5])

    async def test_limit_retry_exhausted_not_cached(self):
        self.codes=['EGW00201']*4
        for _ in range(2):
            with self.assertRaises(ValueError): await kis.get_current_price('A')
        self.assertEqual(len(self.starts),4)

    async def test_other_business_error_no_retry(self):
        self.codes=['OTHER']
        with self.assertRaises(ValueError): await kis.get_current_price('A')
        self.assertEqual(len(self.starts),1)

    async def test_non_mock_url_bypasses_throttle_and_retry(self):
        self.codes=['EGW00201']
        await kis.mock_request(self.client,'get',kis.KIS_REAL_URL+'/test')
        self.assertEqual(self.starts,[0])

    async def test_cancelling_one_consumer_keeps_shared_request(self):
        gate=asyncio.Event();entered=asyncio.Event()
        original=kis._fetch_current_price
        async def pending(*args):
            entered.set();await gate.wait();return await original(*args)
        with patch.object(kis,'_fetch_current_price',pending):
            first=asyncio.create_task(kis.get_current_price('A'))
            second=asyncio.create_task(kis.get_current_price('A'))
            await entered.wait();first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            gate.set();self.assertEqual((await second)['current_price'],123)
        self.assertEqual(len(self.starts),1)


    async def test_http_error_without_limit_code_is_not_retried(self):
        client = type('Client', (), {})()
        client.get = AsyncMock(return_value=httpx.Response(503, json={'msg_cd':'OTHER'},
                               request=httpx.Request('GET',kis.KIS_MOCK_URL+'/test')))
        response = await kis.mock_request(client,'get',kis.KIS_MOCK_URL+'/test')
        self.assertEqual(response.status_code,503)
        self.assertEqual(client.get.await_count,1)

    async def test_token_endpoint_uses_same_spacing_and_single_issuance(self):
        original = self.original_token
        client = type('Client', (), {})()
        async def token(url, **kwargs):
            self.starts.append(self.now)
            return httpx.Response(200,json={'access_token':'fixture-token'},request=httpx.Request('POST',url))
        client.post = token
        async def enter(): return client
        context=type('Context', (), {'__aenter__':lambda _:enter(),
                                     '__aexit__':AsyncMock(return_value=None)})()
        with patch.object(kis,'_mock_token_cache',{}), patch.object(kis.httpx,'AsyncClient',return_value=context):
            values=await asyncio.gather(*(original() for _ in range(8)))
        self.assertEqual(values,['fixture-token']*8)
        self.assertEqual(len(self.starts),1)
        await kis.mock_request(self.client,'get',kis.KIS_MOCK_URL+'/test')
        self.assertEqual(self.starts,[0,.5])
