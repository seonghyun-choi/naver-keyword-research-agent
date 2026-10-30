"""Refresh Trend and blog observations for the frozen Search Ads audit cohort only."""
import datetime as dt,json,time,urllib.request,urllib.error,urllib.parse
from searchad_probe import ROOT,KST,NoRedirect,Client,credentials
from manual import parse_trend
from quality_measurement import trend_diagnosis

def main():
    start=time.perf_counter(); guard=Client(credentials());values={}
    for line in (ROOT.parent/'trend-detector/.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1)
            if k.strip() in ('NAVER_CLIENT_ID','NAVER_CLIENT_SECRET'):values[k.strip()]=v.strip().strip('\"\'')
    guard.values.update(values)
    source=sorted((ROOT/'analysis/searchad').glob('compare-*/report.json'))[-1]
    report=json.loads(source.read_text(encoding='utf-8'));rows=report['rows'];raw=[];logs=[]
    opener=urllib.request.build_opener(NoRedirect());today=dt.datetime.now(KST).date();end=today-dt.timedelta(days=1)
    def call(path,query=None,body=None):
        url='https://naverapihub.apigw.ntruss.com'+path
        if query:url+='?'+urllib.parse.urlencode(query)
        req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,
          headers={'X-NCP-APIGW-API-KEY-ID':values['NAVER_CLIENT_ID'],'X-NCP-APIGW-API-KEY':values['NAVER_CLIENT_SECRET'],'Content-Type':'application/json'})
        for attempt in range(3):
            time.sleep(.15);t=time.perf_counter();status=None
            try:
                with opener.open(req,timeout=25) as res:status=res.status;result=guard.safe(json.loads(res.read()))
            except urllib.error.HTTPError as e:status=e.code;e.close();result={'error':'HTTP_ERROR'}
            except Exception:result={'error':'TRANSPORT_OR_JSON_ERROR'}
            logs.append({'path':path,'http':status,'seconds':round(time.perf_counter()-t,3)})
            if status not in (429,500,502,503,504):break
            if attempt<2:time.sleep(2**(attempt+1))
        raw.append({'path':path,'query':query,'body':body,'response':result})
        if status in (401,403,429):raise RuntimeError('HUB_AUTH_OR_RATE_STOP')
        return result
    for i in range(0,len(rows),5):
        batch=rows[i:i+5];response=call('/search-trend/v1/search',body={'startDate':str(end-dt.timedelta(days=27)),'endDate':str(end),'timeUnit':'date',
            'keywordGroups':[{'groupName':r['query'],'keywords':[r['query']]} for r in batch]})
        for r in batch:
            found=next((x for x in response.get('results',[]) if x.get('title')==r['query']),None)
            r['historical_trend']=r['trend'];r['trend']=trend_diagnosis(found,end,parse_trend)
            r['trend']['checked']=dt.datetime.now(KST).isoformat()
    blogcalls=0;budget=200
    for index,r in enumerate(rows):
        posts={};complete=False;error=False;total=None;pages=0
        for page in range(10):
            if blogcalls>=budget:break
            res=call('/search/v1/blog',query={'query':r['query'],'display':100,'start':1+page*100,'sort':'date'})
            blogcalls+=1;pages+=1
            if not isinstance(res.get('items'),list) or not isinstance(res.get('total'),int):error=True;break
            total=res['total'];dates=[]
            for item in res['items']:
                try:
                    date=dt.datetime.strptime(item['postdate'],'%Y%m%d').date()
                    if date>today:raise ValueError()
                    posts[item['link']]=str(date);dates.append(date)
                except (ValueError,KeyError,TypeError):error=True
            if len(res['items'])<100 or (dates and min(dates)<today-dt.timedelta(days=29)):complete=True;break
            # Supply already abundant: preserve a lower bound, no needless deep crawl.
            if sum(v>=str(today-dt.timedelta(days=6)) for v in posts.values())>30:break
            if budget-blogcalls<=len(rows)-index-1:break
        r['historical_supply']={k:r.get(k) for k in ('blog_total','recent7d_observed','recent30d_observed','supply_status','supply_complete','blog_checked')}
        r.update(blog_total=total,recent7d_observed=sum(v>=str(today-dt.timedelta(days=6)) for v in posts.values()),
          recent30d_observed=sum(v>=str(today-dt.timedelta(days=29)) for v in posts.values()),
          recent30d_complete=complete and not error,supply_complete=bool(not error and (complete or posts and min(posts.values())<str(today-dt.timedelta(days=13)))),
          supply_status='failed' if error else 'ok' if complete else 'censored',blog_checked=dt.datetime.now(KST).isoformat(),blog_pages=pages)
        r['classification']='월간 수요 미확인' if r['total_status']=='unknown' else '월간 수요 값 확보 / '+('최근 방향 자료 확보' if r['trend']['status']=='ok' else '최근 상승 여부 판단 불가')
        print('refresh',index+1,'/',len(rows),'trend',r['trend']['status'],'blog',r['supply_status'],flush=True)
    m=report['metrics'];report['historical_metrics']=m.copy();n=len(rows)
    m.update(trend_nonempty=sum(bool(r['trend'].get('history_points')) for r in rows),trend_sufficient=sum(r['trend']['status']=='ok' for r in rows),
       blog_success=sum(r['supply_status']!='failed' for r in rows),all_three_sample=sum(r['total_status']!='unknown' and r['trend']['status']=='ok' and r['supply_status']!='failed' for r in rows),
       all_three_complete=sum(r['total_status']!='unknown' and r['trend']['status']=='ok' and r['supply_complete'] for r in rows))
    m['B_direction_percent']=round(100*m['trend_sufficient']/n,2);m['C_complete_percent']=round(100*m['all_three_complete']/n,2)
    from run import peak_ram
    m.update(refresh_seconds=round(time.perf_counter()-start,2),refresh_peak_ram_mib=peak_ram(),hub_calls=len(logs),hub_429=sum(l['http']==429 for l in logs),hub_errors=sum(l['http']!=200 for l in logs))
    report['hub_log']=logs;report['mode']='current-cohort-validation';report['checked']=dt.datetime.now(KST).isoformat()
    report['limitations']=['사건과 후보는 10월 27일 고정 표본. 뉴스 신선도는 당시 값이며 최신 뉴스 추천이 아님.',
      '검색광고·트렌드·블로그는 이번 검증 시점 자료. 일별 트렌드는 전일까지 요청.',
      '수요 값 확보와 충분한 수요는 다름. 범위값은 정확한 합계·분위수에서 제외.',
      '공급 수치는 검색 표본/하한값. 최근 문서가 많으면 조기 중단. 최종 추천 점수에는 미반영.']
    folder=ROOT/'analysis/searchad'/('current-'+dt.datetime.now(KST).strftime('%Y%m%d-%H%M%S'));folder.mkdir()
    (folder/'report.json').write_text(json.dumps(guard.safe(report),ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'hub-responses.json').write_text(json.dumps(guard.safe(raw),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(m,ensure_ascii=False));print('report',folder)

if __name__=='__main__':
    try:main()
    except Exception:print('REFRESH_STOPPED: credentials and remote error text suppressed.');raise SystemExit(1)
