"""On-demand 48-hour research. Only NAVER network; isolated, append-only run audit."""
import collections, datetime as dt, hashlib, html, json, math, re, sqlite3, time, sys, webbrowser
from pathlib import Path
from email.utils import parsedate_to_datetime
from unittest.mock import patch
from run import ROOT, KST, iso, write, peak_ram, load_credentials, Meter, d
from editorial import make_brief, relevant
from event_clusters import consolidate, conflict, actions
from manual_scoring import news_metrics, evaluate

SEEDS=d.SEEDS+['기준금리 동결','가상자산 과세','자사주 소각','기업 실적','청년 대출','금융 지원금','공급계약','기업 인수','배당소득 세제','부동산 대책']
LIMIT=40
from quality_clusters import improved_consolidate
from quality_measurement import query_plan,measure_priority

def brief(title):
    title=title.replace('순익','순이익')
    b=make_brief(title)
    if not b:return None
    if b['kind']=='인수':
        # Use an explicitly named acquisition object, not a combined speaker string.
        target=re.search(r'([가-힣A-Za-z][가-힣A-Za-z0-9]{2,20})(?:도|를|을)?\s*(?:공동\s*)?인수',title)
        if target:
            subject=re.sub(r'(도|를|을)$','',target.group(1))
            if subject not in ('기업','업체','회사','공동'):
                b['subject']=subject;b['keyword']=subject+' 인수'
                b['queries']=[subject+' '+s for s in ('인수','인수 배경','인수 진행 상황','인수 영향','인수 승인')]
                b['topic']=subject+' 인수 관련 진행 단계와 독자가 확인할 내용'
    # A named speaker is not necessarily the object of a tax / support policy.
    if b['kind']=='지원' and not re.search(r'은행|금융|대출|소상공인|청년|신혼|주택|신용|보증',b['subject']):return None
    if b['kind']=='과세' and b['subject'] not in ('가상자산','배당소득','양도소득','금융투자소득','상속','증여'):return None
    if b['subject'] in ('국내','글로벌','대규모','최대','역대','여야','국회','장관','은행','증권','한국','미국','중국'):return None
    if '기준금리' in title and re.search('한은|한국은행|금통위',title):
        b['keyword']='한국은행 기준금리';b['subject']='한국은행'
        b['queries']=['한국은행 금리','한국은행 기준금리','기준금리 전망','금리 대출 영향','기준금리 결정 이유']
        if '동결' in title:b['queries']=['한국은행 금리','기준금리 동결','한국은행 기준금리','금리 동결 이유','기준금리 전망']
    if b['subject'] in ('증권사','금융권','기업','업체','주요','은행권') or (b['kind']=='실적' and re.fullmatch(r'[가-힣]{2,3}시',b['subject'])):return None
    b['query_plan']=query_plan(b,title)
    b['queries']=[x['query'] for x in b['query_plan']]
    key=b['keyword']=b['queries'][0]
    b['titles']=[{'type':'정보형','text':key+' 관련 소식과 확인할 핵심 내용'},
                 {'type':'궁금증 해결형','text':key+', 무엇을 먼저 확인해야 할까?'},
                 {'type':'영향 분석형','text':key+' 이슈가 가계·투자 판단에 미치는 영향 살펴보기'}]
    return b

def fetch_news(api, now):
    items={};coverage=[];raw=0
    for seed in SEEDS:
        complete=False;oldest=None;error=None
        for page in range(10 if seed in d.SEEDS else 3):
            res=api.call('/search/v1/news',{'query':seed,'display':100,'start':1+page*100,'sort':'date','format':'json'})
            if res.get('error') or not isinstance(res.get('items'),list):error=res.get('error','INVALID_SCHEMA');break
            rows=res['items'];raw+=len(rows)
            for i in rows:
                try:p=parsedate_to_datetime(i['pubDate']).timestamp()
                except (KeyError,ValueError,TypeError):error='INVALID_TIME';continue
                oldest=p if oldest is None else min(p,oldest)
                if not now-172800<=p<=now:continue
                title=d.clean(i.get('title',''));desc=d.clean(i.get('description',''))
                if not d.financial_relevance(title,desc):continue
                url=d.canonical(i.get('originallink') or i.get('link',''))
                if not url.startswith(('https://','http://')):continue
                items[url]={'url':url,'title':title,'description':desc,'published':p,'publisher':d.publisher(url),'first_seen':now}
            if len(rows)<100 or (oldest is not None and oldest<=now-172800):complete=not error;break
        coverage.append({'query':seed,'pages':page+1,'covered_48h':complete,'oldest':iso(oldest) if oldest else None,'error':error})
        print('news',seed,page+1,'pages',flush=True)
    return list(items.values()),raw,coverage

def cluster(articles,now):
    # Only fresh response articles enter a run. Persisted history cannot influence selection.
    stories=[];index=collections.defaultdict(set)
    for a in sorted(articles,key=lambda a:a['published']):
        candidates=set().union(*(index[t] for t in d.tokens(a['title'])))
        found=None
        for idx in sorted(candidates):
            s=stories[idx]
            if abs(s['published']-a['published'])<=21600 and not conflict(s['title'],a['title']) and d.near_duplicate(s['title'],a['title'],s['description'],a['description']):found=idx;break
        if found is None:
            found=len(stories);stories.append(dict(a,members=[]))
            for t in d.tokens(a['title']):index[t].add(found)
        stories[found]['members'].append(a)
    es=[{'id':str(i),'label':s['title'],'published':s['published']} for i,s in enumerate(stories)]
    groups,edges,near,quality=improved_consolidate(es)
    write(ROOT/"analysis"/"data-quality"/"last-stories.json",es)
    out=[]
    for g in groups:
        ss=[stories[int(i)] for i in g['event_ids']];aa=[a for s in ss for a in s['members']]
        choices=[(s,brief(s['title'])) for s in ss if relevant(s['title'])]
        choices=[(s,b) for s,b in choices if b]
        if not choices:continue
        s,b=max(choices,key=lambda x:len(x[0]['members']))
        n=news_metrics(ss,now);pubs=len({a['publisher'] for a in aa})
        if pubs<2:continue
        out.append({'event_id':g['cluster_id'],'brief':b,'headline':s['title'],'first_seen':iso(min(a['first_seen'] for a in aa)),
                    'news':n,'article_count':len(aa),'unique_stories':len(ss),'publishers':pubs,
                    'sources':[{'name':a['publisher'],'url':a['url'],'title':a['title'],'published':iso(a['published']),
                                'checked':iso(now),'verification':'뉴스 검색 메타데이터 확인; 본문·공식 원문 미검증'} for a in sorted(aa,key=lambda a:-a['published'])[:8]]})
    # Different events remain separate in storage; only diversify the measurement budget.
    out.sort(key=lambda e:(.7*e['news']['topicality']+30*e['news']['freshness'],e['publishers']),reverse=True)
    selected=[];keys=set();categories=collections.Counter()
    for e in out:
        if e['brief']['keyword'] in keys or categories[e['brief']['kind']]>=10:continue
        categories[e['brief']['kind']]+=1
        keys.add(e['brief']['keyword']);selected.append(e)
        if len(selected)==LIMIT:break
    audit={'before_events':quality['before_events'],'additional_merges':[dict(x,a=es[int(x['a'])]['label'],b=es[int(x['b'])]['label']) for x in quality['additional_merges']], 'semantic_rejections':[dict(x,a=es[int(x['a'])]['label'],b=es[int(x['b'])]['label']) for x in quality['semantic_rejections']], 'unique_stories':len(stories),'events':len(groups),'editorial_candidates':len(out),
           'story_to_event_ratio':round(len(groups)/max(1,len(stories)),3),
           'merges':[{'a':es[int(x['a'])]['label'],'b':es[int(x['b'])]['label'],'reason':x['reason']} for x in edges[:20]],
           'near_misses':[{'a':es[int(x['a'])]['label'],'b':es[int(x['b'])]['label'],'reason':x['reason']} for x in near[:20]]}
    return selected,audit

def parse_trend(found,end):
    out={'status':'empty' if found is not None else 'failed','latest_date':'','growth':None,'level':None,'points':[]}
    if not found:return out
    points={p['period']:float(p['ratio']) for p in found.get('data',[]) if isinstance(p.get('ratio'),(int,float)) and 0<=p['ratio']<=100}
    out['points']=[{'date':k,'ratio':v} for k,v in sorted(points.items())]
    if not points:return out
    out.update(status='insufficient',latest_date=max(points))
    prev=[points.get(str(end-dt.timedelta(days=i))) for i in range(1,8)]
    latest=points.get(str(end))
    if latest is not None and all(v is not None for v in prev) and sum(prev)>0:
        out.update(status='ok',growth=(latest-sum(prev)/7)/(sum(prev)/7),level=latest/max(points.values()))
    return out

def measure(api,queries,now):
    end=dt.datetime.fromtimestamp(now,KST).date()-dt.timedelta(days=1);trends={};blogs={}
    for offset in range(0,len(queries),5):
        batch=queries[offset:offset+5]
        res=api.call('/search-trend/v1/search',body={'startDate':str(end-dt.timedelta(days=14)),'endDate':str(end),'timeUnit':'date','keywordGroups':[{'groupName':q,'keywords':[q]} for q in batch]})
        for q in batch:
            found=next((r for r in res.get('results',[]) if r.get('title')==q),None)
            trends[q]=dict(parse_trend(found,end),checked=iso(time.time()),requested_end=str(end),error=res.get('error'))
    today=end+dt.timedelta(days=1);cut=today-dt.timedelta(days=13)
    for q in queries:
        items={};total=None;error=None;complete=False
        for page in range(3):
            res=api.call('/search/v1/blog',{'query':q,'display':100,'start':page*100+1,'sort':'date','format':'json'})
            if res.get('error') or not isinstance(res.get('items'),list) or not isinstance(res.get('total'),int):error=res.get('error','INVALID_SCHEMA');break
            total=res['total'];rows=res['items'];dates=[]
            for i in rows:
                try:day=dt.datetime.strptime(i['postdate'],'%Y%m%d').date()
                except (KeyError,ValueError,TypeError):error='INVALID_POSTDATE';continue
                if day>today:error='FUTURE_POSTDATE';continue
                if not isinstance(i.get('link'),str) or not i['link'].startswith(('https://','http://')):error='INVALID_LINK';continue
                dates.append(day);items[d.canonical(i['link'])]=day
            if len(rows)<100 or (dates and min(dates)<cut):complete=True;break
        recent=sum(today-dt.timedelta(days=6)<=v<=today for v in items.values())
        previous=sum(cut<=v<today-dt.timedelta(days=6) for v in items.values())
        blogs[q]={'status':'failed' if error else 'ok' if complete else 'censored','total':total,'recent7d':recent,
                  'previous7d':previous,'supply_change':recent-previous if complete and not error else None,
                  'recent2_calendar_dates':sum(v>=today-dt.timedelta(days=1) for v in items.values()),
                  'sample':len(items),'checked':iso(time.time()),'error':error,'posts':{k:str(v) for k,v in items.items()}}
        print('keyword',q,'trend',trends[q]['status'],'blog',blogs[q]['status'],flush=True)
    return trends,blogs

def choose(events,trends,blogs):
    evaluated=[]
    for e in events:
        qs=[]
        for q in e['brief']['queries']:
            t,b=trends[q],blogs[q]
            ev=evaluate(e['news'],t,b,e['publishers'],iso(e['news']['first_published'])[:10])
            qs.append(dict(query=q,trend=t,blog=b,**ev))
        # Missing demand never makes an obscure zero-blog phrase the best query.
        # Stable sorting keeps the primary intent when all demand evidence is absent.
        qs.sort(key=lambda x:(x['qualified'],x['score'] is not None,x['score'] or 0,bool(x['trend']['points'])),reverse=True)
        e['queries']=qs;e['best']=qs[0];evaluated.append(e)
    writes=sorted((e for e in evaluated if e['best']['qualified']),key=lambda e:-e['best']['score'])[:3]
    watches=sorted((e for e in evaluated if not e['best']['qualified']),key=lambda e:-e['best']['watch_priority'])[:3]
    return writes,watches

def render(report,path):
    esc=lambda s:html.escape(str(s))
    def card(e,rank):
        b=e['best'];n=e['news'];trend=b['trend'];blog=b['blog'];brief=e['brief']
        text=f"최근 보도 주제: {e['headline']}. {e['publishers']}개 출처에서 확인했습니다."
        if b['qualified']:text+=' 일별 상대 관심 상승과 비교적 적은 블로그 공급 표본이 함께 확인됐습니다.'
        else:text+=' 작성 기회 검증 전의 관심 후보입니다. '+ '; '.join(b['reasons'])
        fields={'최초 기사 시각':iso(n['first_published']),'시스템 최초 발견':e['first_seen'],'최근 기사 시각':iso(n['latest_published']),
                '기사 수 (고유 URL / 중복 제거)':f"{e['article_count']} / {e['unique_stories']}",
                '0–6 / 6–24 / 24–48시간 중복 제거 기사': ' / '.join(map(str,n['bins'])), '고유 언론사 도메인 수':e['publishers'],
                '검색 관심 수준':f"자체 기간 최고 대비 {trend['level']:.1%}" if trend['level'] is not None else '검색 데이터 부족',
                '검색 추세':f"전일 지수 / 이전 7일 평균 {trend['growth']:+.1%}" if trend['growth'] is not None else '검색 데이터 부족',
                '블로그 공급':f"최근 7개 날짜 표본 {blog['recent7d']}건 · 검색 total {blog['total']} · {blog['status']}",
                '최신 문서 밀도 proxy':blog.get('latest_density_proxy','미측정'),
                '최근 공급 변화':blog['supply_change'] if blog['supply_change'] is not None else '표본 제한으로 비교 불가',
                'Demand-Supply Opportunity':b['demand_supply'] if b['demand_supply'] is not None else '산출 불가',
                '최근 화제성':n['topicality'],'전체 추천 점수':b['score'] if b['score'] is not None else '산출 불가',
                '신뢰도':b['confidence'],'판단':b['decision']}
        content=''.join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k,v in fields.items())
        titles=''.join(f"<li><b>{esc(t['type'])}</b> {esc(t['text'])}</li>" for t in brief['titles'])
        links=''.join(f'<li><a href="{esc(s["url"])}">{esc(s["title"])}</a> — {esc(s["name"])}</li>' for s in e['sources'][:4])
        details=[dict({k:q[k] for k in ('query','demand_supply','score','reasons','parts')}, intent=next((p['type'] for p in brief.get('query_plan',[]) if p['query']==q['query']),''),search_status=q['trend']['status'],search_diagnosis=q['trend'].get('diagnosis'),blog_status=q['blog']['status']) for q in e['queries']]
        return f'<article><span class="badge">{esc(b["decision"])}</span><h3>{rank}. {esc(b["query"])}</h3><p>{esc(brief["topic"])}</p><p>{esc(text)}</p><dl>{content}</dl><h4>예상 검색어 5개</h4><p>{esc(" · ".join(brief["queries"]))}</p><h4>추천 제목</h4><ul>{titles}</ul><h4>글에 다룰 핵심 내용</h4><ul>'+''.join('<li>'+esc(v)+'</li>' for v in brief['outline'])+f'</ul><h4>참고 뉴스</h4><p>검색 메타데이터 확인. 공식 원문·뉴스 본문은 미검증이므로 작성 전 교차 확인이 필요합니다.</p><ul>{links}</ul><details><summary>검색어별 점수와 탈락 이유</summary><pre>{esc(json.dumps(details,ensure_ascii=False,indent=2))}</pre></details></article>'
    sections=''
    for key,title in [('write','지금 작성 추천'),('watch','관심 후보')]:
        sections+=f'<h2>{title} · {len(report[key])}개</h2>'
        sections+=''.join(card(e,i+1) for i,e in enumerate(report[key])) or '<p>기준을 충족한 후보가 없습니다. 결과를 채우기 위해 기준을 낮추지 않았습니다.</p>'
    rejected=''
    for title,rows in report['rejections'].items():
        rejected+='<h3>'+esc(title)+'</h3>'+(''.join('<p>'+esc(r['query']+': '+'; '.join(r['reasons']))+'</p>' for r in rows) or '<p>이 유형으로 확인된 사례 없음. 데이터 부족을 낮은 수요로 해석하지 않습니다.</p>')
    doc='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>오늘 쓸 주제 찾기</title><style>body{font:16px/1.7 "Malgun Gothic",sans-serif;background:#f3f6fa;color:#17283c;max-width:1000px;margin:40px auto;padding:0 20px}h1{font-size:30px}article{background:white;padding:26px;margin:20px 0;border:1px solid #dce4ee;border-radius:16px}h3{margin:12px 0}dl{display:grid;grid-template-columns:1fr 1.3fr;gap:8px}dt{color:#52637b}dd{margin:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.badge{background:#e1eef9;padding:5px 12px;border-radius:20px}a{color:#175b98}details{border-top:1px solid #ddd;padding-top:14px}@media(max-width:600px){dl{display:block}dd{margin-bottom:12px}}</style>'''
    funnel_names={'unique_article_urls':'전체 기사 (고유 URL)','deduplicated_stories':'중복 제거 기사','events':'Event','primary_candidate_events':'1차 후보 Event','evaluated_events':'상세 평가 Event','generated_query_candidates':'생성 검색어 후보','unique_queries_requested':'실제 조회 검색어','search_nonempty':'검색 데이터 확보','search_usable':'충분한 검색 시계열','blog_success':'블로그 공급 응답 성공','blog_complete':'블로그 14개 날짜 조회 완료','dso_computable':'DSO 산출 가능 (잘린 공급 표본 포함)','dso_complete_supply':'DSO 산출 + 공급 구간 확보','write_count':'지금 작성 추천','watch_count':'관심 후보'}
    funnel='<article><h2>'+esc(report.get('verdict','분석 결과'))+'</h2><dl>'+''.join('<dt>'+esc(label)+'</dt><dd>'+esc(report['metrics'].get(key,'미기록'))+'</dd>' for key,label in funnel_names.items())+'</dl><p>조회 성공과 충분한 데이터 확보는 다릅니다. 잘린 블로그 표본의 DSO는 잠정값이며 작성 추천을 통과시키지 않습니다.</p></article>'
    m=report['metrics'];den=max(1,m['unique_queries_requested']);gen=max(1,m.get('generated_unique_queries',m['generated_query_candidates']))
    funnel+='<p>실제 조회 '+str(den)+'개 기준: 검색 데이터 '+format(m['search_nonempty']/den,'.1%')+' · 충분한 시계열 '+format(m['search_usable']/den,'.1%')+' · 블로그 응답 '+format(m['blog_success']/den,'.1%')+' · DSO 산출 '+format(m.get('dso_computable',0)/den,'.1%')+'</p>'
    funnel+='<p>생성 전체 '+str(gen)+'개 기준: 검색 데이터 '+format(m['search_nonempty']/gen,'.1%')+' · 충분한 시계열 '+format(m['search_usable']/gen,'.1%')+' · DSO 산출 '+format(m.get('dso_computable',0)/gen,'.1%')+'. 미조회 후보도 별도 기록했습니다.</p>'
    if not report['write']:funnel+='<p>충분한 검색 시계열 중 블로그 공급 기준 초과: '+str(m.get('high_supply_with_usable_search','미기록'))+'개. 나머지의 기회 존재 여부까지 배제한 결과는 아닙니다.</p>'
    doc+='<h1>오늘 지금 쓰기 좋은 저경쟁·고수요 키워드 TOP 3</h1><p>'+esc(report['as_of'])+' 기준 · 최근 48시간 · 직접 실행 분석</p><p>‘고수요’는 절대 검색량으로 확인할 수 없습니다. 일별 상대 관심과 블로그 검색 표본을 비교한 후보이며, 실시간 검색량이나 수익을 보장하지 않습니다.</p>'+funnel+sections+'<h2>추천에서 제외된 사례</h2>'+rejected
    doc+='<details><summary>데이터 한계 · 실행 측정 · 클러스터링 점검</summary><pre>'+esc(json.dumps({k:report[k] for k in ('limitations','metrics','coverage','clustering')},ensure_ascii=False,indent=2))+'</pre></details></html>'
    path.write_text(doc,encoding='utf8')

def main():
    import msvcrt
    (ROOT/'data').mkdir(exist_ok=True)
    lock=open(ROOT/'data'/'manual.lock','a+b');lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:print('이미 분석 중입니다.');return 2
    now=time.time();cpu=time.process_time();runid=dt.datetime.fromtimestamp(now,KST).strftime('%Y%m%d-%H%M%S')
    output=ROOT/'outputs'/('manual-'+runid);output.mkdir(parents=True)
    db=d.database(ROOT/'data'/'manual.sqlite')
    db.executescript('CREATE TABLE IF NOT EXISTS manual_runs(id TEXT PRIMARY KEY,at REAL,status TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS manual_articles(url TEXT PRIMARY KEY,first_seen REAL,payload TEXT);')
    db.execute('INSERT INTO manual_runs VALUES(?,?,?,NULL)',(runid,now,'running'));db.commit()
    try:
        load_credentials();meter=Meter();api=d.API(db)
        with patch.object(d.urllib.request,'build_opener',meter.opener):
            articles,raw,coverage=fetch_news(api,now)
            if not articles:raise RuntimeError('수집된 기사가 없습니다. API 오류 및 연결 상태를 확인하세요.')
            for a in articles:
                old=db.execute('SELECT first_seen FROM manual_articles WHERE url=?',(a['url'],)).fetchone()
                if old:a['first_seen']=old[0]
                db.execute('INSERT INTO manual_articles VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET payload=excluded.payload',(a['url'],a['first_seen'],json.dumps(a,ensure_ascii=False)))
            db.commit()
            events,audit=cluster(articles,now)
            write(output/'articles.json',articles)
            trends,blogs,measurement=measure_priority(api,events,now,parse_trend)
            queries=measurement['requested_queries']
            write(output/'measurements-raw.json',measurement)
        writes,watches=choose(events,trends,blogs)
        flat=[q for e in events for q in e['queries']]
        rejections={
            '검색 관심은 높지만 경쟁이 높은 사례':[q for q in flat if q['relative_interest'] is not None and q['relative_interest']>=.6 and (q['blog']['recent7d'] or 0)>30][:3],
            '블로그 공급은 적지만 상대 검색 관심도 낮은 사례':[q for q in flat if q['relative_interest'] is not None and q['relative_interest']<.3 and q['blog']['status']=='ok' and q['blog']['recent7d']<=30][:3],
            '검색 데이터 부족으로 판단을 보류한 사례':[q for q in flat if q['trend']['status']!='ok'][:3]}
        metrics={'raw_news_results':raw,'unique_article_urls':len(articles),'deduplicated_stories':audit['unique_stories'],'events':audit['events'],
                 'evaluated_events':len(events),'generated_query_candidates':sum(len(e['brief']['queries']) for e in events),
                 'unique_queries_requested':len(queries),'search_nonempty':sum(bool(t['points']) for t in trends.values()),
                 'search_usable':sum(t['status']=='ok' for t in trends.values()),'blog_success':sum(b['status'] in ('ok','censored') for b in blogs.values()),
                 'primary_candidate_events':audit['editorial_candidates'],'search_requested':len(queries),'generated_unique_queries':measurement['generated_unique'],
                 'dso_computable':sum(q['demand_supply'] is not None for q in {q['query']:q for q in flat}.values()),
                 'blog_complete':sum(b['status']=='ok' for b in blogs.values()),
                 'dso_complete_supply':sum(q['demand_supply'] is not None and q['blog']['status']=='ok' for q in {q['query']:q for q in flat}.values()),
                 'search_diagnosis':dict(collections.Counter(t.get('diagnosis') for t in trends.values())),
                 'write_count':len(writes),'watch_count':len(watches),'api_calls':len(meter.calls),'api_by_path':dict(collections.Counter(c['path'] for c in meter.calls)),
                 'api_errors':sum(c['status']!=200 for c in meter.calls),'llm_calls':0,'gpu_used':False,'peak_ram_mib':peak_ram(),
                 'cpu_seconds':round(time.process_time()-cpu,2),'elapsed_seconds':round(time.time()-now,2)}
        metrics['acquisition_rate']=round(metrics['search_nonempty']/max(1,len(queries)),4)
        metrics['sufficient_series_rate']=round(metrics['search_usable']/max(1,len(queries)),4)
        metrics['dso_rate']=round(metrics['dso_computable']/max(1,len(queries)),4)
        metrics['blog_success_rate']=round(metrics['blog_success']/max(1,len(queries)),4)
        metrics['dso_generated_rate']=round(metrics['dso_computable']/max(1,measurement['generated_unique']),4)
        metrics['acquisition_generated_rate']=round(metrics['search_nonempty']/max(1,measurement['generated_unique']),4)
        metrics['sufficient_generated_rate']=round(metrics['search_usable']/max(1,measurement['generated_unique']),4)
        metrics['high_supply_with_usable_search']=sum(q['trend']['status']=='ok' and (q['blog'].get('recent7d') or 0)>30 for q in {q['query']:q for q in flat}.values())
        verdict='작성 추천 있음' if writes else '데이터 부족으로 판단 불가' if metrics['dso_complete_supply']<measurement['generated_unique'] or not all(c['covered_48h'] for c in coverage) else '검증 결과 실제 기회 없음 (평가한 후보 범위)'
        report={'verdict':verdict,'as_of':iso(now),'run_id':runid,'write':writes,'watch':watches,'events':events,'rejections':rejections,'metrics':metrics,
                'coverage':coverage,'clustering':audit,'api_log':meter.calls,'limitations':[
                    '뉴스 API 페이지 제한으로 최근 48시간 전체 기사 수집을 보장하지 않습니다. 각 검색어 coverage를 확인하세요.',
                    '사건 최초 발생이 아니라 검색에서 관측된 최초 기사 시각입니다. 소급 수집 빈도는 실시간 탐지 속도가 아닙니다.',
                    '보수적 제목 기반 클러스터링은 과분리 가능성이 남습니다. 같은 기업만으로 병합하지 않습니다.',
                    'Search Trend는 전일까지 요청한 일별 상대지수입니다. 빈 응답은 검색량 0이 아닙니다.',
                    '검색 지수의 자체 기간 최고 대비 수준은 절대 수요를 뜻하지 않습니다. 작성 추천에는 상대 관심 상승을 추가 요구합니다.',
                    '블로그 total은 검색 매칭 추정치입니다. 최근 7일/이전 7일은 날짜 단위 검색 표본입니다. 우선 100건, 필요한 후보만 최대 1000건까지 조회하며 잘린 값은 하한 표본입니다.',
                    '출처 도메인 수는 독립 취재 수를 보장하지 않습니다. 뉴스 검색 메타데이터만 확인했으며 공식 원문 검증은 자동화하지 않습니다.',
                    '최대 40개 Event에서 핵심·설명 검색어를 우선 조회하고, 검색 신호가 있는 최대 10개 Event의 나머지 의도를 추가 조회합니다. 뉴스 상위 후보 밖의 기회가 누락될 수 있습니다.',
                    '점수 65·DSO 40·최근 블로그 30건 기준은 사전 고정 휴리스틱입니다. 검색 유입 성과로 보정된 예측 모델이 아닙니다.' ]}
        write(output/'report.json',report);render(report,output/'report.html')
        write(ROOT/'manual-latest.json',report);render(report,ROOT/'manual-latest.html')
        with db:db.execute('UPDATE manual_runs SET status=?,payload=? WHERE id=?',('complete',json.dumps(report,ensure_ascii=False),runid))
        print(json.dumps(metrics,ensure_ascii=False));print(str(output/'report.html'))
        if '--open' in sys.argv:webbrowser.open((ROOT/'manual-latest.html').as_uri())
        return 0
    except Exception as exc:
        with db:db.execute('UPDATE manual_runs SET status=?,payload=? WHERE id=?',('failed',json.dumps({'error':type(exc).__name__+': '+str(exc)},ensure_ascii=False),runid))
        raise
    finally:
        db.close();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()

if __name__=='__main__':raise SystemExit(main())
