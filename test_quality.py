import unittest,datetime as dt
from quality_clusters import same_event,signature
from quality_measurement import query_plan,trend_diagnosis,measure_priority
from manual import brief,parse_trend

class QualityTests(unittest.TestCase):
    def pair(self,a,b):return same_event({'label':a,'published':1000},{'label':b,'published':1200})
    def test_matching_metric_and_quarter(self):self.assertTrue(self.pair('가온기업 3Q 영업익 948억원','가온기업 3분기 영업이익 948억…매출 성장')[0])
    def test_quarter_vs_cumulative(self):self.assertFalse(self.pair('가온기업 3분기 누적 순익 948억원','가온기업 3분기 순익 948억원')[0])
    def test_different_entities(self):self.assertFalse(self.pair('가온기업 3분기 순익 948억원','다른기업 3분기 순익 948억원')[0])
    def test_metric_labels_matter(self):self.assertFalse(self.pair('가온기업 3분기 매출 948억원','가온기업 3분기 순익 948억원')[0])
    def test_composite_amounts(self):
        self.assertEqual(signature('가온기업 3분기 매출 1조1799억원')['metrics']['매출'],11799)
        self.assertFalse(self.pair('가온기업 3분기 매출 1조1799억원','가온기업 3분기 매출 1조5000억원')[0])
    def test_forecast_not_actual(self):self.assertFalse(self.pair('가온기업 3분기 순익 948억원 예상','가온기업 3분기 순익 948억원 발표')[0])
    def test_acquisition_stage(self):self.assertFalse(self.pair('가온기업 새빛은행 인수 추진','가온기업 새빛은행 인수 완료')[0])
    def test_launch_rate_conflict(self):self.assertFalse(self.pair('가온증권 발행어음 출시 4%','가온증권 발행어음 출시 5%')[0])
    def test_short_primary_product(self):
        b=brief('메리츠증권 발행어음 출시 금리 안내');self.assertEqual(b['queries'][0],'메리츠증권 발행어음')
        self.assertEqual([p['type'] for p in b['query_plan']],['핵심','설명','원인','영향','전망'])
    def test_sparse_stays_sparse(self):
        r=trend_diagnosis({'data':[{'period':'2026-10-26','ratio':100}]},dt.date(2026,10,26),parse_trend)
        self.assertEqual(r['diagnosis'],'baseline_dates_missing');self.assertEqual(len(r['missing_baseline_dates']),7);self.assertIsNone(r['growth'])
    def test_invalid_date_is_not_silently_used(self):
        r=trend_diagnosis({'data':[{'period':'garbage','ratio':100}]},dt.date(2026,10,26),parse_trend)
        self.assertEqual(r['status'],'invalid')
    def test_long_history_does_not_change_level_definition(self):
        end=dt.date(2026,10,26);data=[{'period':str(end-dt.timedelta(days=i)),'ratio':100 if i==20 else 50} for i in range(28)]
        self.assertEqual(trend_diagnosis({'data':data},end,parse_trend)['level'],1)
    def test_priority_budget_and_sparse_blog_one_page(self):
        class API:
            def __init__(self):self.calls=[]
            def call(self,path,query=None,body=None):
                self.calls.append((path,query,body))
                if body:return {'results':[{'title':x['groupName'],'data':[]} for x in body['keywordGroups']]}
                return {'total':10000,'items':[{'link':f'https://example.com/{i}','postdate':'20261026'} for i in range(100)]}
        api=API();b=brief('가온기업 3분기 영업이익 발표');ts,bs,a=measure_priority(api,[{'brief':b}],1793098000,parse_trend)
        self.assertEqual(len(a['requested_queries']),2);self.assertEqual(sum(b['status']=='not_requested' for b in bs.values()),3)
        self.assertEqual(sum('/blog' in x[0] for x in api.calls),2)
        self.assertTrue(all(v['status']!='ok' for v in ts.values()))

if __name__=='__main__':unittest.main()
