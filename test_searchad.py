import unittest, tempfile, json, urllib.error
from pathlib import Path
from searchad_probe import Client,FIELDS,count,counts,signature,normalized,quantiles

class Response:
    status=200
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return b'{"keywordList": [{"relKeyword":"test", "monthlyPcQcCnt":"<10", "monthlyMobileQcCnt":100}]}'

class Tests(unittest.TestCase):
    def test_range(self):
        c=counts({'monthlyPcQcCnt':'<10','monthlyMobileQcCnt':100})
        self.assertIsNone(c['monthlyTotalQcCnt']);self.assertEqual(c['total_status'],'censored')
        self.assertEqual(c['pc']['upper_exclusive'],10)
    def test_exact(self): self.assertEqual(counts({'monthlyPcQcCnt':'20','monthlyMobileQcCnt':100})['monthlyTotalQcCnt'],120)
    def test_invalid(self):
        for value in [None,True,-1,'bad',3.5]:self.assertEqual(count(value)['status'],'invalid')
    def test_signature(self):
        import hmac,hashlib,base64
        self.assertEqual(signature('fixture-secret','123'),base64.b64encode(hmac.new(b'fixture-secret',b'123.GET./keywordstool',hashlib.sha256).digest()).decode())
    def test_query_identity(self):
        self.assertEqual(normalized('LG 실적'),normalized('LG실적'))
        self.assertNotEqual(normalized('LG 실적'),normalized('LG'))
    def test_cache_and_redaction(self):
        class Opener:
            calls=0
            def open(self,*args,**kwargs): self.calls+=1;return Response()
        with tempfile.TemporaryDirectory() as t:
            op=Opener();c=Client(dict(zip(FIELDS,['fixture-license','fixture-secret','12345'])),Path(t),op,lambda n:None)
            c.get('test');c.get('test');self.assertEqual(op.calls,1)
            self.assertNotIn('fixture-secret',json.dumps(c.safe({'message':'fixture-secret'})))
            self.assertNotIn('fixture-secret',json.dumps(c.logs))
    def test_backoff(self):
        class Opener:
            calls=0
            def open(self,*args,**kwargs):
                self.calls+=1
                raise urllib.error.HTTPError('https://api.searchad.naver.com',429,'fixture-secret',{},None)
        with tempfile.TemporaryDirectory() as t:
            waits=[];op=Opener();c=Client(dict(zip(FIELDS,['a-license','fixture-secret','12345'])),Path(t),op,waits.append)
            self.assertEqual(c.get('test')['http'],429);self.assertEqual(op.calls,3)
            self.assertIn(2,waits);self.assertIn(4,waits)
            self.assertNotIn('fixture-secret',json.dumps(c.logs));self.assertFalse(list(Path(t).rglob('*.json')))
    def test_no_auth_retry(self):
        class Opener:
            calls=0
            def open(self,*args,**kwargs):self.calls+=1;raise urllib.error.HTTPError('https://api.searchad.naver.com',403,'fixture-secret',{},None)
        with tempfile.TemporaryDirectory() as t:
            op=Opener();c=Client(dict(zip(FIELDS,['a-license','fixture-secret','12345'])),Path(t),op,lambda n:None)
            c.get('test');self.assertEqual(op.calls,1)

if __name__=='__main__':unittest.main()
