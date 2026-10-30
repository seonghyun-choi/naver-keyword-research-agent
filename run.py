"""Independent one-shot briefing. No model clients, posting, or scheduler registration."""
import collections,ctypes,datetime as dt,hashlib,html,json,os,re,sqlite3,sys,time,uuid
from pathlib import Path
from email.utils import parsedate_to_datetime
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'vendor'))
import detector as d
from event_clusters import consolidate
from editorial import make_brief,relevant,PATTERNS
from scoring import news_metrics,evaluate
KST=dt.timezone(dt.timedelta(hours=9))
def iso(t):return dt.datetime.fromtimestamp(t,KST).isoformat(timespec='seconds')
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(path)
def peak_ram():
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(x,ctypes.c_size_t) for x in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    c=Counters();c.cb=ctypes.sizeof(c)
    kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
    fn=ctypes.windll.psapi.GetProcessMemoryInfo;fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    if not fn(kernel.GetCurrentProcess(),ctypes.byref(c),c.cb):raise OSError('GetProcessMemoryInfo failed')
    return round(c.PeakWorkingSetSize/1024**2,2)

def load_credentials():
    # Explicitly read only these two keys; never copy or print secrets.
    for line in (ROOT.parent/'trend-detector'/'.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1)
            if k.strip() in ('NAVER_CLIENT_ID','NAVER_CLIENT_SECRET'):os.environ.setdefault(k.strip(),v.strip().strip('\"\''))

class Meter:
    def __init__(self):self.calls=[];self.original=d.urllib.request.build_opener
    def opener(self,*args,**kwargs):
        inner=self.original(*args,**kwargs);meter=self
        class Opener:
            def open(self,request,*a,**kw):
                # Only NAVER API HUB. Headers/credentials never enter logs.
                from urllib.parse import urlsplit
                u=urlsplit(request.full_url)
                if u.hostname!='naverapihub.apigw.ntruss.com':raise ValueError('NON_NAVER_NETWORK_FORBIDDEN')
                item={'path':u.path,'at':iso(time.time()),'status':'started'};meter.calls.append(item)
                try:r=inner.open(request,*a,**kw);item['status']=getattr(r,'status',200);return r
                except Exception as e:item['status']=getattr(e,'code',type(e).__name__);raise
        return Opener()

def collect(db,api,now,cfg):
    session=uuid.uuid4().hex
    db.execute('INSERT INTO sessions VALUES(?,?,NULL,?)',(session,now,'briefing-v1'))
    cycle=db.execute("INSERT INTO cycles(session,started,status,config,bootstrap) VALUES(?,?,'running','briefing-v1',1)",(session,now)).lastrowid;db.commit()
    discovery=d.Discovery(db,now);coverage=[];counts=collections.Counter();fetched=0;urls=set()
    for seed in d.SEEDS:
        covered=False;error=None;oldest=None
        for page in range(cfg['news_pages_per_seed']):
            db.commit()  # The reused API adapter starts its own usage transaction.
            result=api.call('/search/v1/news',{'query':seed,'display':100,'start':1+100*page,'sort':'date','format':'json'})
            if result.get('error') or not isinstance(result.get('items'),list):error=result.get('error','INVALID_SCHEMA');break
            items=result['items'];fetched+=len(items);valid=0
            for item in items:
                try:published=parsedate_to_datetime(item['pubDate']).timestamp()
                except (KeyError,ValueError,TypeError):counts['invalid_time']+=1;continue
                valid+=1;oldest=published if oldest is None else min(oldest,published)
                if published<now-43200:continue
                if published>now:counts['future_or_after_cutoff']+=1;continue
                try:urls.add(d.canonical(item.get('originallink') or item['link']))
                except (ValueError,KeyError):continue
                counts[discovery.ingest(item,cycle,time.time(),True)]+=1
            if valid!=len(items):error='INVALID_PUBLICATION_TIME'
            if not error and (len(items)<100 or (oldest is not None and oldest<=now-43200)):covered=True;break
        coverage.append({'query':seed,'pages':page+1,'window_covered':covered,'oldest':iso(oldest) if oldest else None,'error':error})
        db.commit();print('news',seed,'pages',page+1,'coverage',covered,flush=True)
    db.execute('UPDATE cycles SET ended=?,status=?,coverage=? WHERE id=?',(time.time(),'complete' if all(c['window_covered'] for c in coverage) else 'partial',json.dumps(coverage),cycle))
    db.execute('UPDATE sessions SET ended=? WHERE id=?',(time.time(),session));db.commit()
    return {'raw_results':fetched,'unique_window_urls_before_financial_filter':len(urls),'ingest_counts':dict(counts),'coverage':coverage,'cycle':cycle}

def candidates(db,now,coverage):
    articles=[dict(r) for r in db.execute('SELECT * FROM articles WHERE published>=? AND published<=?',(now-43200,now))]
    byevent=collections.defaultdict(list)
    for a in articles:byevent[a['event_id']].append(a)
    events=[]
    for row in db.execute('SELECT * FROM events'):
        if row['id'] not in byevent:continue
        e=dict(row);e['published']=min(a['published'] for a in byevent[e['id']]);events.append(e)
    groups,merges,_=consolidate(events);byid={e['id']:e for e in events};out=[]
    for group in groups:
        aa=[a for eid in group['event_ids'] for a in byevent[eid]]
        label=max(aa,key=lambda a:len(a['title']))['title']
        if not relevant(label):continue
        brief=make_brief(label)
        if not brief:continue
        # Dedup again across consolidated events; earliest publication represents story.
        ss=[]
        for a in sorted(aa,key=lambda a:a['published']):
            if not any(abs(s['published']-a['published'])<21600 and d.near_duplicate(s['title'],a['title'],s['description'],a['description']) for s in ss):ss.append(a)
        metrics=news_metrics(ss,aa,now,coverage)
        first=min(byid[e]['first_seen'] for e in group['event_ids'])
        out.append({'event_id':group['cluster_id'],'lineage':group['event_ids'],'label':label,'brief':brief,'first_seen':iso(first),
                    'first_published':iso(metrics['first_published']),'news':metrics,
                    'sources':[{'name':a['publisher'],'url':a['url'],'title':a['title'],'published':iso(a['published']),
                                'checked_at':iso(a['first_seen']),'evidence_type':'NAVER 뉴스 검색 메타데이터; 원문 사실 검증 아님'} for a in sorted(aa,key=lambda a:a['published'],reverse=True)[:8]],
                    'official_sources':[],'official_note':'이번 실행에서는 공식 원문을 확인하지 않음. 아래 작성 항목의 기관에서 확인 필요.'})
    out.sort(key=lambda c:(c['news']['current_2h']>0,c['news']['recent_6h']*2+c['news']['previous_6h'],c['news']['unique_sources']),reverse=True)
    # Avoid spending validation quota on multiple variants of one query.
    unique=[];seen=set()
    for c in out:
        if c['brief']['keyword'] in seen:continue
        seen.add(c['brief']['keyword']);unique.append(c)
    return unique,{'window_articles':len(articles),'original_events':len(events),'consolidated_events':len(groups),'merge_edges':len(merges),'editorial_candidates':len(unique)}

def scoped_news(candidate,db,api,now):
    """Recheck a finite event query instead of treating a capped broad query as complete."""
    query=candidate['brief']['keyword'];items={};covered=False;error=None;raw=0
    for page in range(3):
        result=api.call('/search/v1/news',{'query':query,'display':100,'start':1+100*page,'sort':'date','format':'json'})
        if result.get('error') or not isinstance(result.get('items'),list):error=result.get('error','INVALID_SCHEMA');break
        fetched=result['items'];raw+=len(fetched);oldest=None
        for item in fetched:
            try:
                published=parsedate_to_datetime(item['pubDate']).timestamp()
                url=d.canonical(item.get('originallink') or item['link'])
            except (ValueError,KeyError,TypeError):error='INVALID_ITEM';continue
            oldest=published if oldest is None else min(oldest,published)
            if not now-43200<=published<=now:continue
            title=d.clean(item['title']);desc=d.clean(item.get('description',''))
            brief=make_brief(title)
            if not brief or brief['subject']!=candidate['brief']['subject'] or brief['kind']!=candidate['brief']['kind']:continue
            pair=[{'id':'a','label':candidate['label'],'published':candidate['news']['first_published']},{'id':'b','label':title,'published':published}]
            if len(consolidate(pair)[0])!=1 and title!=candidate['label']:continue
            items[url]={'url':url,'title':title,'description':desc,'published':published,'first_seen':time.time(),'publisher':d.publisher(url)}
        db.execute('INSERT INTO scoped_news_audit VALUES(?,?,?,?)',(now,query,page,json.dumps(result,ensure_ascii=False)));db.commit()
        if not error and (len(fetched)<100 or (oldest is not None and oldest<=now-43200)):covered=True;break
    # All retained broad-query items were already classified into this event.
    lineage=candidate['lineage'];placeholders=','.join('?' for _ in lineage)
    base=[dict(r) for r in db.execute(f'SELECT * FROM articles WHERE event_id IN ({placeholders}) AND published BETWEEN ? AND ?',(*lineage,now-43200,now))]
    for a in base:items[a['url']]=a
    aa=list(items.values());ss=[]
    for a in sorted(aa,key=lambda a:a['published']):
        if not any(abs(s['published']-a['published'])<21600 and d.near_duplicate(s['title'],a['title'],s['description'],a['description']) for s in ss):ss.append(a)
    candidate['news']=news_metrics(ss,aa,now,covered)
    candidate['first_published']=iso(candidate['news']['first_published'])
    candidate['news_scope']={'query':query,'pages':page+1,'raw_results':raw,'window_covered':covered,'error':error,
                             'note':'해당 검색어 결과 범위의 시간 커버리지. 네이버 전체 뉴스 완전성 보장은 아님.'}
    candidate['sources']=[{'name':a['publisher'],'url':a['url'],'title':a['title'],'published':iso(a['published']),
                           'checked_at':iso(a['first_seen']),'evidence_type':'뉴스 검색 메타데이터; 원문 사실 검증 아님'} for a in sorted(aa,key=lambda a:a['published'],reverse=True)[:8]]
    candidate['article_urls']=[a['url'] for a in aa]

def finish_cards(all_candidates,db,api,now,cfg):
    selected=all_candidates[:cfg['validation_event_limit']]
    db.execute('CREATE TABLE IF NOT EXISTS scoped_news_audit(at REAL,query TEXT,page INTEGER,payload TEXT)');db.commit()
    for c in selected:
        scoped_news(c,db,api,now)
        print('scope',c['brief']['keyword'],'complete',c['news_scope']['window_covered'],flush=True)
    # Reuse official daily search/blog measurement code on this DB exclusively.
    d.CONFIG['validation_candidates_per_cycle']=cfg['validation_event_limit']
    d.validate(db,api,[[{'query':c['brief']['keyword']}] for c in selected],now)
    results=[]
    for c in selected:
        query=c['brief']['keyword'];t=d.cached(db,'trend',query,time.time());b=d.cached(db,'blog',query,time.time())
        c.update(search=t,blog=b,**evaluate(c['news'],t,b,cfg))
        c['search_note']='일별 상대 검색지수; 실시간·절대 검색량 아님'
        c['blog_note']='검색 API의 최근 7일 작성일 기준 표본; 전체 경쟁 난이도나 시간별 공급량이 아님'
        c['query_measurements']=[{'query':q,'measured':q==query} for q in c['brief']['queries']]
        c['why_now']=f"최근 2시간 고유 기사 {c['news']['current_2h']}건, 직전 2시간 {c['news']['previous_2h']}건. 출처 도메인 {c['news']['unique_sources']}개."
        if c['decision']=='관찰':c['why_now']+=' 뉴스 확산과 낮은 공급을 바탕으로 관찰할 후보이며 검색 상승을 확정하지 않았습니다.'
        results.append(c)
    eligible=[c for c in results if c['decision']!='패스']
    eligible.sort(key=lambda c:(c['decision']=='지금 작성',c['opportunity_score'] if c['decision']=='지금 작성' else c['observation_priority']),reverse=True)
    top=eligible[:cfg['max_results']]
    for i,c in enumerate(top,1):c['rank']=i
    return top,results

def render(report,path):
    esc=lambda v:html.escape(str(v))
    out=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>경제 블로그 주제 추천</title><style>body{background:#f4f6fa;color:#17243a;font:16px/1.8 system-ui;max-width:1050px;margin:30px auto;padding:0 20px}.card{background:white;border:1px solid #d6dce7;border-radius:14px;padding:24px;margin:24px 0}.tag{background:#fff2cc;padding:8px}h1,h2{line-height:1.4}a{color:#235fc0;overflow-wrap:anywhere}pre{white-space:pre-wrap;font-size:13px}.muted{color:#526079}</style>',
         '<h1>경제·재테크 블로그 주제 추천</h1>',f'<p>분석 기준 {esc(report["as_of"])} · 최근 12시간 / 최근 6시간 우선</p>',
         '<p class="tag">자동 실행 비활성 · 본문 생성/LLM/자동 게시 없음 · 관찰은 검색 수요 미검증 후보입니다.</p>']
    if not report['top3']:out.append('<div class="card"><h2>이번 실행의 추천 주제: 0개</h2><p>고정 기준을 통과한 후보가 없습니다. 기준을 낮춰 TOP 3를 채우지 않았습니다.</p></div>')
    for c in report['top3']:
        b=c['brief'];n=c['news'];score=c['opportunity_score'] if c['decision']=='지금 작성' else c['observation_priority']
        out.extend([f'<article class="card"><span class="tag">{esc(c["decision"])} · {esc(c["status"])}</span><h2>{c["rank"]}. {esc(b["keyword"])}</h2>',
                    '<p>'+esc(b['topic'])+'</p><p>'+esc(c['why_now'])+'</p>',
                    f'<p>최초 발견: {esc(c["first_seen"])}<br>수집 자료의 최초 발행: {esc(c["first_published"])}<br>기사 URL {n["article_count"]}건 / 고유 기사 {n["unique_stories"]}건 / 출처 도메인 {n["unique_sources"]}개</p>',
                    f'<p>{"추천 점수" if c["decision"]=="지금 작성" else "관찰 우선순위"}: <b>{score}</b> / 신뢰도 {c["confidence"]:.0%} (통계적 성공 확률 아님)<br>Opportunity Score: {esc(c["opportunity_score"] if c["opportunity_score"] is not None else "미산정")}</p>',
                    '<h3>검색 관심 신호</h3><p>'+esc(c['search_note'])+'</p><pre>'+esc(json.dumps(c['search'],ensure_ascii=False,indent=2))+'</pre>',
                    '<h3>블로그 공급·경쟁</h3><p>'+esc(c['blog_note'])+'</p><pre>'+esc(json.dumps(c['blog'],ensure_ascii=False,indent=2))+'</pre>',
                    '<h3>추천 제목</h3><ul>'+''.join('<li>'+esc(t)+'</li>' for t in b['titles'])+'</ul>',
                    '<h3>예상 검색어</h3><p>'+esc(' / '.join(b['queries']))+'</p><p class="muted">대표 검색어만 실제 측정. 나머지는 규칙 기반 검색어 후보.</p>',
                    '<h3>반드시 확인할 내용</h3><ul>'+''.join('<li>'+esc(x)+'</li>' for x in b['outline'])+'</ul>',
                    '<h3>출처</h3><p>'+esc(c['official_note'])+'</p><ul>'])
        for s in c['sources']:out.append('<li><a href="'+esc(s['url'])+'">'+esc(s['title'])+'</a> — '+esc(s['name'])+' · '+esc(s['published'])+'</li>')
        out.append('</ul><details><summary>점수 구성·보류 사유·뉴스 시간 분포</summary><pre>'+esc(json.dumps({k:c[k] for k in ['score_components','observation_components','reasons','news']},ensure_ascii=False,indent=2))+'</pre></details></article>')
    out.append('<details><summary>실행 결과와 미선정 사유 집계</summary><pre>'+esc(json.dumps({'performance':report['performance'],'counts':report['counts'],'rejections':report['rejection_counts'],'coverage':report['collection']['coverage']},ensure_ascii=False,indent=2))+'</pre></details></html>')
    path.parent.mkdir(exist_ok=True,parents=True);path.write_text(''.join(out),encoding='utf8')

def main():
    start=time.perf_counter();cpu=time.process_time();now=time.time();cfg=json.loads((ROOT/'config.json').read_text(encoding='utf8'))
    run_id=dt.datetime.fromtimestamp(now,KST).strftime('%Y%m%d-%H%M%S');folder=ROOT/'outputs'/run_id
    # Exclusive OS lock prevents overlapping independent runs.
    import msvcrt
    lock=open(ROOT/'data'/'run.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise SystemExit('Another briefing is running')
    db=d.database(ROOT/'data'/'briefing.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS briefing_runs(id TEXT PRIMARY KEY,at REAL,status TEXT,report TEXT)')
    db.execute('INSERT INTO briefing_runs VALUES(?,?,?,NULL)',(run_id,now,'running'));db.commit()
    try:
        load_credentials();api=d.API(db);meter=Meter()
        with patch.object(d.urllib.request,'build_opener',meter.opener):
            collection=collect(db,api,now,cfg)
            cs,counts=candidates(db,now,all(c['window_covered'] for c in collection['coverage']))
            print('events',counts,'validation',min(len(cs),cfg['validation_event_limit']),flush=True)
            top,evaluated=finish_cards(cs,db,api,now,cfg)
        counts.update(validated_candidates=len(evaluated),eligible_candidates=sum(c['decision']!='패스' for c in evaluated),top3_count=len(top))
        base_urls={r[0] for r in db.execute('SELECT url FROM articles WHERE published BETWEEN ? AND ?',(now-43200,now))}
        scoped_urls={url for c in evaluated for url in c.get('article_urls',[])}
        counts['unique_articles_including_scoped']=len(base_urls|scoped_urls)
        counts['scoped_news_raw_results']=sum(c.get('news_scope',{}).get('raw_results',0) for c in evaluated)
        counts['scoped_complete_candidates']=sum(c.get('news_scope',{}).get('window_covered',False) for c in evaluated)
        report={'version':cfg['version'],'run_id':run_id,'as_of':iso(now),'completed_at':iso(time.time()),'config':cfg,
                'collection':collection,'counts':counts,'top3':top,'rejection_counts':dict(collections.Counter(r for c in evaluated for r in c['reasons'])),
                'performance':{'peak_ram_mib':peak_ram(),'elapsed_seconds':round(time.perf_counter()-start,2),'cpu_seconds':round(time.process_time()-cpu,2),
                               'http_requests':len(meter.calls),'api_counts':dict(collections.Counter(c['path'] for c in meter.calls)),'llm_calls':0},
                'limitations':['뉴스 발행시각 기반 추정; 실제 발생 시각 아님','검색은 지연된 일별 상대지수','출처 도메인은 독립 취재 또는 언론사 소유주 수와 다름','공식 원문 사실 검증은 수행하지 않음','점수는 유입 확률이 아님']}
        write(folder/'report.json',report);write(folder/'candidate-audit.json',evaluated);write(folder/'http-log.json',meter.calls)
        render(report,folder/'index.html');render(report,ROOT/'latest.html');write(ROOT/'latest.json',report)
        db.execute('UPDATE briefing_runs SET status=?,report=? WHERE id=?',('complete',str(folder/'report.json'),run_id));db.commit()
        print(json.dumps({'output':str(folder),'performance':report['performance'],'counts':counts,'top3':[{'keyword':c['brief']['keyword'],'decision':c['decision'],'score':c['opportunity_score'],'watch_score':c['observation_priority']} for c in top]},ensure_ascii=False,indent=2))
    except Exception as e:
        db.execute('UPDATE briefing_runs SET status=? WHERE id=?',('failed:'+type(e).__name__,run_id))
        db.execute("UPDATE cycles SET status='failed',ended=? WHERE status='running'",(time.time(),));db.commit()
        write(folder/'failure.json',{'error_type':type(e).__name__,'elapsed_seconds':time.perf_counter()-start,'http_calls':getattr(locals().get('meter'),'calls',[])})
        raise
    finally:db.close();lock.close()
if __name__=='__main__':main()
