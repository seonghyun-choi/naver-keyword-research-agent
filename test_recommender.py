import unittest,sys,json,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
from scoring import evaluate,news_metrics
from editorial import make_brief,relevant
from event_clusters import consolidate

class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.cfg=json.loads((Path(__file__).parent/'config.json').read_text())
        self.m={'ready':True,'current_2h':5,'growth':2,'first_published':1700000000,'news_component':1,'early_component':.9,'source_component':1}
        self.b={'status':'ok','sample':100,'total':100,'recent7d':2}
        self.t={'status':'ok','growth':2,'latest_date':'2030-01-01'}
    def test_missing_search_not_verified(self):
        r=evaluate(self.m,None,self.b,self.cfg);self.assertEqual(r['decision'],'관찰');self.assertIsNone(r['opportunity_score'])
    def test_event_after_search_not_verified(self):
        self.t['latest_date']='2000-01-01';self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'관찰')
    def test_search_empty_not_zero(self):
        self.t.update(status='empty',growth=None);self.assertIsNone(evaluate(self.m,self.t,self.b,self.cfg)['opportunity_score'])
    def test_high_supply_pass(self):
        self.b['recent7d']=100;self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'패스')
    def test_censored_never_low_competition(self):
        self.b['status']='censored';self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'패스')
    def test_empty_blog_not_proof_low_supply(self):
        self.b['sample']=0;self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'패스')
    def test_declining_news_pass(self):
        self.m['growth']=-.3;self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'패스')
    def test_incomplete_coverage_pass(self):
        self.m['ready']=False;self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'패스')
    def test_supported_opportunity(self):
        self.assertEqual(evaluate(self.m,self.t,self.b,self.cfg)['decision'],'지금 작성')
    def test_single_sample_no_momentum(self):
        m=news_metrics([{'published':9900}],[{'publisher':'a'}],10000,True)
        self.assertIsNone(m['growth']);self.assertIsNone(m['velocity']);self.assertIsNone(m['news_momentum'])
    def test_headline_not_copied_or_numbers_added(self):
        title='가온기업, 3분기 영업이익 999억원 발표'
        b=make_brief(title);self.assertEqual(len(b['queries']),5);self.assertEqual(len(b['titles']),3)
        self.assertTrue(all(title!=t and '999' not in t for t in b['titles']));self.assertEqual(b['keyword'],'가온기업 실적')
    def test_unknown_entity_supported(self):self.assertEqual(make_brief('새빛금융, 자사주 매입 완료')['keyword'],'새빛금융 자사주')
    def test_general_crime_excluded(self):self.assertFalse(relevant('마약 운반 공무원 대출 문제'))
    def test_different_numbers_not_merged(self):
        es=[{'id':str(i),'label':f'가온기업 영구채 {i}억원 발행','published':1000} for i in (100,200)]
        self.assertEqual(len(consolidate(es)[0]),2)
    def test_schedule_is_disabled(self):
        self.assertFalse(self.cfg['schedule']['enabled']);self.assertEqual(self.cfg['schedule']['times'],['08:30','14:00','20:30'])
    def test_tax_concept_preferred_over_speaker(self):
        self.assertEqual(make_brief('박홍근 배당소득 과세 검토')['keyword'],'배당소득 과세')
    def test_schedule_latest_only(self):
        import datetime as dt
        from scheduler import latest_due,KST
        now=dt.datetime(2026,10,23,21,0,tzinfo=KST)
        self.assertEqual(latest_due(now,['08:30','14:00','20:30']).hour,20)
        self.assertEqual(latest_due(now.replace(hour=7),['08:30','14:00','20:30']).day,22)
    def test_disabled_scheduler_does_not_launch(self):
        from unittest.mock import patch
        import scheduler
        with patch('subprocess.run',side_effect=AssertionError('must not launch')):
            self.assertEqual(scheduler.main(),2)
    def test_scoped_news_excludes_other_subject_and_stores_audit(self):
        import sqlite3,datetime as dt
        from email.utils import format_datetime
        from run import scoped_news
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.executescript('CREATE TABLE articles(event_id TEXT,published REAL); CREATE TABLE scoped_news_audit(at REAL,query TEXT,page INTEGER,payload TEXT);')
        title='가온기업 자사주 매입 완료';now=1700000000
        def item(name):return {'title':name,'description':'','originallink':'https://example.com/'+str(len(name)),'pubDate':format_datetime(dt.datetime.fromtimestamp(now-60,dt.timezone.utc))}
        class FakeAPI:
            def call(self,*args,**kwargs):return {'items':[item(title),item('다른기업 자사주 매입 완료')]}
        candidate={'label':title,'brief':make_brief(title),'news':{'first_published':now-60},'lineage':['a']}
        scoped_news(candidate,db,FakeAPI(),now)
        self.assertTrue(candidate['news_scope']['window_covered']);self.assertEqual(candidate['news']['article_count'],1)
        self.assertEqual(db.execute('SELECT count(*) FROM scoped_news_audit').fetchone()[0],1);db.close()

if __name__=='__main__':unittest.main()
