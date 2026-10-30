"""Synthetic fixtures only; never write operational databases or call network."""
import unittest,datetime as dt
from unittest.mock import patch
from manual_scoring import evaluate,news_metrics
from manual import brief,parse_trend,cluster,choose,measure

class ManualTests(unittest.TestCase):
    def setUp(self):
        self.news={'bins':[0,4,3],'topicality':80,'freshness':.8}
        self.trend={'status':'ok','level':1,'growth':1,'latest_date':'2026-10-22'}
        self.blog={'status':'ok','total':10,'recent7d':1}
    def ev(self):return evaluate(self.news,self.trend,self.blog,4,'2026-10-21')
    def test_yesterday_without_recent_news_can_write(self):self.assertTrue(self.ev()['qualified'])
    def test_missing_is_not_low_demand(self):
        self.trend['status']='empty';self.assertIsNone(self.ev()['score']);self.assertIsNone(self.ev()['demand_supply'])
    def test_low_supply_alone_not_opportunity(self):
        self.trend['level']=.01;self.trend['growth']=-.9;self.assertFalse(self.ev()['qualified']);self.assertLess(self.ev()['demand_supply'],1)
    def test_high_supply_penalized(self):
        before=self.ev()['score'];self.blog['recent7d']=300;self.assertLess(self.ev()['score'],before);self.assertFalse(self.ev()['qualified'])
    def test_truncated_supply_not_low_competition(self):
        self.blog['status']='censored';self.assertFalse(self.ev()['qualified'])
    def test_future_event_not_verified_by_old_search(self):
        self.trend['latest_date']='2026-10-20';self.assertFalse(self.ev()['qualified'])
    def test_48h_bins_and_first_latest(self):
        now=200000;articles=[{'published':now-h*3600} for h in (1,8,25)]
        self.assertEqual(news_metrics(articles,now)['bins'],[1,1,1])
    def test_sparse_trend_never_zero_filled(self):
        t=parse_trend({'data':[{'period':'2026-10-22','ratio':100}]},dt.date(2026,10,22))
        self.assertEqual(t['status'],'insufficient');self.assertIsNone(t['growth'])
    def test_within_query_scale_invariance(self):
        end=dt.date(2026,10,22)
        points=[{'period':str(end-dt.timedelta(days=i)),'ratio':50 if i==0 else 25} for i in range(15)]
        a=parse_trend({'data':points},end)
        b=parse_trend({'data':[dict(p,ratio=p['ratio']/10) for p in points]},end)
        self.assertEqual(a['level'],b['level']);self.assertEqual(a['growth'],b['growth'])
    def test_five_queries_and_three_typed_titles(self):
        b=brief('한국은행 기준금리 동결 결정');self.assertEqual(len(set(b['queries'])),5);self.assertEqual(len(b['titles']),3)
        self.assertTrue(all('동결 결정' not in t['text'] for t in b['titles']))
    def test_person_tax_subject_not_used(self):self.assertIsNone(brief('박홍근 과세 검토 발표'))
    def test_acquisition_object_not_joint_speakers(self):
        self.assertEqual(brief('전북은행·핀다, 대원저축은행 공동 인수')['keyword'],'대원저축은행 인수')
        self.assertEqual(brief('다우키움그룹, 문학동네 인수 추진')['keyword'],'문학동네 인수')
    def test_earnings_abbreviation_not_loan_support(self):
        self.assertEqual(brief('우리금융, 3분기 순익 성장…기업대출 증가')['kind'],'실적')
    def test_no_schedule_even_if_config_enabled(self):
        import scheduler
        with patch('subprocess.run',side_effect=AssertionError('scheduler launched')):self.assertEqual(scheduler.main(),2)
    def test_all_queries_measured_without_llm(self):
        class API:
            def __init__(self):self.calls=[]
            def call(self,path,query=None,body=None):
                self.calls.append((path,query,body))
                return {'results':[{'title':q['groupName'],'data':[]} for q in body['keywordGroups']]} if body else {'total':0,'items':[]}
        api=API();queries=['fixture '+str(i) for i in range(5)]
        ts,bs=measure(api,queries,1792670000)
        self.assertEqual(set(ts),set(queries));self.assertEqual(set(bs),set(queries));self.assertEqual(len(api.calls),6)
    def test_amounts_and_contract_stage_not_merged(self):
        from event_clusters import consolidate
        es=[{'id':str(i),'label':s,'published':1000} for i,s in enumerate(['가온기업 반도체 공급계약 100억원 체결','가온기업 반도체 공급계약 200억원 체결','가온기업 반도체 인수 우선협상 선정','가온기업 반도체 인수 계약 체결'])]
        self.assertEqual(len(consolidate(es)[0]),4)

if __name__=='__main__':unittest.main()
