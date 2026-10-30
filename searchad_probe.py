"""Isolated Search Ads data audit. Never imports or changes recommendation scores."""
import argparse, base64, collections, datetime as dt, hashlib, hmac, html, json, re
import time, urllib.request, urllib.error, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KST = dt.timezone(dt.timedelta(hours=9))
FIELDS = ('NAVER_SEARCHAD_ACCESS_LICENSE', 'NAVER_SEARCHAD_SECRET_KEY', 'NAVER_SEARCHAD_CUSTOMER_ID')

def credentials(path=ROOT/'.env.searchad.local'):
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if key.strip() in FIELDS: values[key.strip()] = value.strip().strip('\"\'')
    if not all(values.get(k) for k in FIELDS): raise ValueError('SEARCHAD_CONFIG_MISSING')
    if not values[FIELDS[2]].isdigit(): raise ValueError('SEARCHAD_CUSTOMER_ID_INVALID')
    return values

def signature(secret, timestamp, method='GET', uri='/keywordstool'):
    return base64.b64encode(hmac.new(secret.encode(), f'{timestamp}.{method}.{uri}'.encode(), hashlib.sha256).digest()).decode()

def count(value):
    if isinstance(value, bool): return {'raw': value, 'status':'invalid', 'value':None}
    if isinstance(value, int) and value >= 0: return {'raw':value,'status':'exact','value':value}
    if isinstance(value, str) and re.fullmatch(r'\d+', value.strip()): return {'raw':value,'status':'exact','value':int(value)}
    if isinstance(value, str) and re.fullmatch(r'<\s*\d+', value.strip()):
        return {'raw':value,'status':'censored','value':None,'lower_inclusive':0,'upper_exclusive':int(re.sub(r'\D','',value))}
    return {'raw':value,'status':'invalid','value':None}

def counts(row):
    pc, mobile = count(row.get('monthlyPcQcCnt')), count(row.get('monthlyMobileQcCnt'))
    exact = pc['status']==mobile['status']=='exact'
    return {'pc':pc,'mobile':mobile,'monthlyTotalQcCnt':pc['value']+mobile['value'] if exact else None,
            'total_status':'exact' if exact else 'censored' if all(x['status']!='invalid' for x in (pc,mobile)) else 'unknown'}

def normalized(q):
    # Only whitespace/case equivalence; never substitute names or related keywords.
    return re.sub(r'\s+', '', q).casefold()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class Client:
    def __init__(self, values, cache=ROOT/'data/searchad-cache', opener=None, sleep=time.sleep):
        self.values=values; self.cache=Path(cache); self.opener=opener or urllib.request.build_opener(NoRedirect())
        self.sleep=sleep; self.logs=[]; self.last=0; self.cache_hits=0
    def safe(self, obj):
        # Prevent even a remote error/echo from leaking credentials to artifacts.
        if isinstance(obj,dict): return {self.safe(k):self.safe(v) for k,v in obj.items()}
        if isinstance(obj,list): return [self.safe(v) for v in obj]
        if isinstance(obj,str):
            for value in self.values.values(): obj=obj.replace(value,'[REDACTED]')
        return obj
    def get(self, query):
        key=normalized(query)
        if not key or len(key)>30 or re.search(r'[,·]',key): return {'status':'invalid_query','http':None,'keywordList':[]}
        day=dt.datetime.now(KST).date().isoformat()
        path=self.cache/day/(hashlib.sha256(key.encode()).hexdigest()+'.json')
        if path.exists(): self.cache_hits+=1; return json.loads(path.read_text(encoding='utf-8'))
        for attempt in range(3):
            self.sleep(max(0,0.6-(time.monotonic()-self.last)))
            stamp=str(int(time.time()*1000)); self.last=time.monotonic()
            headers={'X-Timestamp':stamp,'X-API-KEY':self.values[FIELDS[0]],'X-Customer':self.values[FIELDS[2]],
                     'X-Signature':signature(self.values[FIELDS[1]],stamp)}
            req=urllib.request.Request('https://api.searchad.naver.com/keywordstool?'+urllib.parse.urlencode({'hintKeywords':key,'showDetail':1}),headers=headers)
            started=time.perf_counter(); status=None
            try:
                with self.opener.open(req,timeout=25) as response:
                    status=response.status; body=json.loads(response.read())
                valid=isinstance(body,dict) and isinstance(body.get('keywordList'),list) and all(isinstance(x,dict) for x in body['keywordList'])
                result={'status':'ok' if valid else 'invalid_schema','http':status,'checked':dt.datetime.now(KST).isoformat(),
                        'keywordList':self.safe(body['keywordList']) if valid else []}
            except urllib.error.HTTPError as error:
                status=error.code; result={'status':'http_error','http':status,'keywordList':[]}
                error.close()  # Do not log remote error bodies or request headers.
            except Exception:
                result={'status':'transport_or_json_error','http':status,'keywordList':[]}
            self.logs.append({'query':query,'http':status,'attempt':attempt+1,'seconds':round(time.perf_counter()-started,3),'status':result['status']})
            if result['status']=='ok':
                path.parent.mkdir(parents=True,exist_ok=True)
                tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8'); tmp.replace(path)
                return result
            if status not in (429,500,502,503,504): return result
            if attempt<2: self.sleep(2**(attempt+1))
        return result

def quantiles(values):
    if not values: return {'n':0}
    values=sorted(values)
    def p(q):
        i=(len(values)-1)*q; lo=int(i); hi=min(lo+1,len(values)-1)
        return round(values[lo]+(values[hi]-values[lo])*(i-lo),2)
    return dict(n=len(values), minimum=values[0],P10=p(.1),P25=p(.25),median=p(.5),P75=p(.75),P90=p(.9),maximum=values[-1])

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--mode',choices=['smoke','compare'],default='smoke'); args=parser.parse_args()
    started=time.perf_counter(); client=Client(credentials())
    baseline=json.loads((ROOT/'manual-latest.json').read_text(encoding='utf-8'))
    source_folder=ROOT/'outputs'/('manual-'+baseline['run_id'].removeprefix('manual-'))
    raw=json.loads((source_folder/'measurements-raw.json').read_text(encoding='utf-8'))
    lookup={x['query']:(e,x) for e in baseline['events'] for x in e['queries']}
    queries=raw['requested_queries']
    if args.mode=='smoke': queries=queries[:6]+[q for q in ('가상자산 과세','국고채 금리') if q in queries and q not in queries[:6]]
    if args.mode=='compare':
        gate=ROOT/'analysis/searchad/smoke-success.json'
        if not gate.exists(): raise ValueError('SMOKE_TEST_REQUIRED')
        if json.loads(gate.read_text())['day']!=str(dt.datetime.now(KST).date()): raise ValueError('FRESH_SMOKE_TEST_REQUIRED')
    rows=[]; responses=[]
    for q in queries:
        result=client.get(q); responses.append({'query':q,'response':result})
        matches=[r for r in result['keywordList'] if normalized(r.get('relKeyword',''))==normalized(q)]
        match=matches[0] if len(matches)==1 else None
        e,old=lookup[q]; trend=old['trend']; blog=old['blog']
        today=dt.datetime.fromisoformat(baseline['as_of']).date()
        dates=list(blog.get('posts',{}).values())
        recent30=sum(str(today-dt.timedelta(days=29))<=v<=str(today) for v in dates)
        row={'query':q,'http':result['http'],'api_status':result['status'],'matching':'whitespace_case_equivalent' if match and match['relKeyword']!=q else 'exact' if match else 'no_unique_match',
             'relKeyword':match.get('relKeyword') if match else None,**counts(match or {}),
             'compIdx_ad_only':match.get('compIdx') if match else None,'plAvgDepth_ad_only':match.get('plAvgDepth') if match else None,
             'related_count':len(result['keywordList']),'trend':trend,'blog_total':blog.get('total'),'recent7d_observed':blog.get('recent7d'),
             'recent30d_observed':recent30,'recent30d_complete':bool(dates and min(dates)<str(today-dt.timedelta(days=29))),
             'supply_status':blog['status'],'supply_complete':blog.get('previous7d_complete',False),'blog_checked':blog.get('checked'),
             'event_id':e['event_id'],'event_headline':e['headline'],'first_seen':e['first_seen'],'news':e['news'],
             'source_count':e['publishers'],'article_count':e['article_count'],'source_as_of':baseline['as_of'],
             'searchad_checked':result.get('checked'),'classification':'기본 수요 미확인'}
        if row['total_status']!='unknown': row['classification']='월간 수요 값 확보 / '+('최근 방향 자료 확보' if trend['status']=='ok' else '최근 상승 여부 판단 불가')
        rows.append(row)
        print('checked',len(rows),'/',len(queries),'HTTP',result['http'],'match',row['matching'],flush=True)
        if result['status']!='ok' and result['status']!='invalid_query' and result['http']!=400: break
    n=len(rows); valid=[r for r in rows if r['total_status']!='unknown']; exact=[r for r in rows if r['total_status']=='exact']
    sufficient=lambda r:r['trend']['status']=='ok'
    supply=lambda r:r['supply_status'] in ('ok','censored')
    metrics={'evaluated':n,'planned':len(queries),'normal_responses':sum(r['api_status']=='ok' for r in rows),
             'pc_available':sum(r['pc']['status']!='invalid' for r in rows),'mobile_available':sum(r['mobile']['status']!='invalid' for r in rows),
             'monthly_exact':len(exact),'monthly_range':sum(r['total_status']=='censored' for r in rows),
             'censored_pc':sum(r['pc']['status']=='censored' for r in rows),'censored_mobile':sum(r['mobile']['status']=='censored' for r in rows),
             'related_available':sum(r['related_count']>0 for r in rows),
             'related_unique':len({x.get('relKeyword') for r in responses for x in r['response']['keywordList']}),
             'trend_nonempty':sum(bool(r['trend'].get('history_points')) for r in rows),'trend_sufficient':sum(map(sufficient,rows)),
             'blog_success':sum(map(supply,rows)), 'all_three_sample':sum(sufficient(r) and supply(r) for r in valid),
             'all_three_complete':sum(sufficient(r) and r['supply_complete'] for r in valid),
             'A_demand_percent':round(100*len(valid)/max(1,n),2),'B_direction_percent':round(100*sum(map(sufficient,rows))/max(1,n),2),
             'C_complete_percent':round(100*sum(sufficient(r) and r['supply_complete'] for r in valid)/max(1,n),2),
             'api_requests':len(client.logs),'cache_hits':client.cache_hits,'http429':sum(x['http']==429 for x in client.logs),
             'errors':sum(x['status']!='ok' for x in client.logs),'seconds':round(time.perf_counter()-started,2),'llm_calls':0}
    from run import peak_ram
    metrics['peak_ram_mib']=peak_ram()
    report={'mode':args.mode,'baseline_as_of':baseline['as_of'],'checked':dt.datetime.now(KST).isoformat(),
            'limitations':['기존 고정 후보의 추가 데이터 감사',
                          'Search Trend와 블로그는 과거 저장값. 검색광고와 시점이 달라 현재 기회를 확정하지 않음.',
                          '공백·영문 대소문자 차이만 허용. 연관어 수요를 원래 검색어에 대입하지 않음.',
                          '블로그 기간 수치는 표본 관측값. 공급 완전성과 성공을 구분.',
                          '월간 수요는 범위값 포함 여부를 구분. 점수/추천 기준 변경 없음.'],
            'metrics':metrics,'distributions':{'pc':quantiles([r['pc']['value'] for r in rows if r['pc']['status']=='exact']),
            'mobile':quantiles([r['mobile']['value'] for r in rows if r['mobile']['status']=='exact']),
            'total':quantiles([r['monthlyTotalQcCnt'] for r in exact])},'rows':rows,'api_log':client.logs}
    folder=ROOT/'analysis/searchad'/(args.mode+'-'+dt.datetime.now(KST).strftime('%Y%m%d-%H%M%S'));folder.mkdir(parents=True)
    for name,obj in [('report.json',report),('responses.json',responses)]:
        (folder/name).write_text(json.dumps(client.safe(obj),ensure_ascii=False,indent=2),encoding='utf-8')
    columns=['query','relKeyword','monthlyTotalQcCnt','compIdx_ad_only','related_count','blog_total','recent7d_observed','recent30d_observed','supply_status','classification']
    table='<table><tr>'+''.join('<th>'+c+'</th>' for c in columns)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(c)))+'</td>' for c in columns)+'</tr>' for r in rows)+'</table>'
    page='<!doctype html><meta charset="utf-8"><title>검색광고 데이터 검증</title><style>body{font-family:Malgun Gothic;margin:32px}td,th{padding:8px;border:1px solid #ddd}table{border-collapse:collapse}pre{white-space:pre-wrap}</style><h1>검색광고 데이터 검증 — 추천 아님</h1><p>기존 후보의 과거 Trend·블로그 측정과 새 월간 수요의 비교. 광고 경쟁도는 블로그 경쟁도가 아닙니다.</p><pre>'+html.escape(json.dumps(metrics,ensure_ascii=False,indent=2))+'</pre>'+table
    (folder/'report.html').write_text(client.safe(page),encoding='utf-8')
    if args.mode=='smoke' and n==len(queries) and all(r['api_status']=='ok' for r in rows) and valid:
        (ROOT/'analysis/searchad/smoke-success.json').write_text(json.dumps({'day':str(dt.datetime.now(KST).date()),'report':str(folder)}),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False));print('report',folder)

if __name__=='__main__':
    try: main()
    except Exception: print('SEARCHAD_AUDIT_STOPPED: configuration or local processing error; no credential details logged.');raise SystemExit(1)
