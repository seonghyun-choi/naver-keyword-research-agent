"""Budgeted, ordered measurements. Sparse data stays sparse, no volume imputation."""
import datetime as dt,re,time,collections
from run import KST,iso,d

def query_plan(b,title):
    subject=b['subject'];kind=b['kind']
    product=next((p for p in ('발행어음','주택담보대출','주담대','영구채','회사채','자사주','기준금리','가상자산 과세') if p in title),None)
    core=b['keyword']
    if product and kind in ('금리','채권','서비스','지원'):
        core=subject+' '+product if product not in subject else subject
    if product=='기준금리' and re.search('한국은행|한은|금통위',title):core='한국은행 기준금리'
    synonyms={'실적':'영업이익','자사주':'자사주 소각','과세':'과세 유예','인수':'인수 이유','금리':'금리 비교','지원':'지원 대상','채권':'채권 금리','계약':'수주 공시','서비스':'이용 방법','환율':'환율 전망'}
    explanation=subject+' '+synonyms.get(kind,'뜻')
    if product and product not in explanation:explanation=core+' 조건'
    candidates=[('핵심',core),('설명',explanation),('원인',core+' 이유'),('영향',core+' 영향'),('전망',core+' 전망')]
    seen=set();out=[]
    for typ,q in candidates:
        q=' '.join(q.split())
        if q in seen:q=core+' '+{'설명':'뜻','원인':'배경','영향':'변화','전망':'예상'}.get(typ,'정보')
        seen.add(q);out.append({'query':q,'type':typ,'priority':len(out)+1,'basis':'규칙 기반 예상 검색어; 검색 로그 아님'})
    return out

def trend_diagnosis(found,end,parse):
    if found is None:return dict(parse(None,end),diagnosis='missing_result_or_api_error',missing_baseline_dates=[],invalid_points=0)
    valid=[];invalid=0
    for p in found.get('data',[]):
        try:
            date=dt.date.fromisoformat(p['period']);value=p['ratio']
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not 0<=value<=100 or date>end:raise ValueError()
            valid.append(p)
        except (KeyError,TypeError,ValueError):invalid+=1
    # Maintain original 15-day level definition and eight required daily observations.
    recent=[p for p in valid if p['period']>=str(end-dt.timedelta(days=14))]
    result=parse({'data':recent},end);points={p['period']:p['ratio'] for p in recent}
    missing=[str(end-dt.timedelta(days=i)) for i in range(1,8) if str(end-dt.timedelta(days=i)) not in points]
    why='complete' if result['status']=='ok' else 'empty_response' if not valid else 'latest_day_missing' if str(end) not in points else 'baseline_dates_missing' if missing else 'baseline_zero'
    result.update(diagnosis=why,missing_baseline_dates=missing,invalid_points=invalid,history_points=valid)
    if invalid:result.update(status='invalid',diagnosis='invalid_response_points',growth=None,level=None)
    return result

def measure_priority(api,events,now,parse):
    end=dt.datetime.fromtimestamp(now,KST).date()-dt.timedelta(days=1)
    plans=[e['brief']['query_plan'] for e in events]
    ordered=list(dict.fromkeys(p[i]['query'] for i in (0,1) for p in plans))
    trends={};blogs={};raw=[];weekly={};budget=200;blog_calls=0
    def search(qs):
        for offset in range(0,len(qs),5):
            batch=qs[offset:offset+5]
            result=api.call('/search-trend/v1/search',body={'startDate':str(end-dt.timedelta(days=27)),'endDate':str(end),'timeUnit':'date','keywordGroups':[{'groupName':q,'keywords':[q]} for q in batch]})
            raw.append({'kind':'trend','queries':batch,'response':result})
            for q in batch:
                found=next((x for x in result.get('results',[]) if x.get('title')==q),None)
                trends[q]=dict(trend_diagnosis(found,end,parse),checked=iso(time.time()),requested_end=str(end),error=result.get('error'))
    search(ordered)
    # Secondary intents only for at most ten events with some observed core demand.
    extended=[p for p in plans if any(trends[x['query']]['points'] for x in p[:2])][:10]
    extra=list(dict.fromkeys(p[i]['query'] for i in (2,3,4) for p in extended if p[i]['query'] not in trends))
    search(extra);ordered+=extra
    # Weekly context can reveal longer-run data availability, never replace daily gates.
    sparse=[p[0]['query'] for p in plans if trends[p[0]['query']]['status']!='ok'][:10]
    for offset in range(0,len(sparse),5):
        batch=sparse[offset:offset+5]
        result=api.call('/search-trend/v1/search',body={'startDate':str(end-dt.timedelta(days=55)),'endDate':str(end),'timeUnit':'week','keywordGroups':[{'groupName':q,'keywords':[q]} for q in batch]})
        raw.append({'kind':'weekly_context','queries':batch,'response':result})
        for q in batch:weekly[q]=next((x.get('data',[]) for x in result.get('results',[]) if x.get('title')==q),[])
    today=end+dt.timedelta(days=1);cut=today-dt.timedelta(days=13);cut7=today-dt.timedelta(days=6)
    # Full daily evidence first receives deep supply budget; sparse queries get one page.
    supply_order=sorted(ordered,key=lambda q:trends[q]['status']!='ok')
    for qi,q in enumerate(supply_order):
        items={};total=None;error=None;complete=False;pages=0;oldest=None;newest=None
        for page in range(10):
            if blog_calls>=budget:break
            result=api.call('/search/v1/blog',{'query':q,'display':100,'start':page*100+1,'sort':'date','format':'json'});blog_calls+=1;pages+=1
            raw.append({'kind':'blog','query':q,'page':page+1,'response':result})
            if result.get('error') or not isinstance(result.get('items'),list) or not isinstance(result.get('total'),int):error=result.get('error','INVALID_SCHEMA');break
            total=result['total'];rows=result['items'];dates=[]
            for item in rows:
                try:
                    date=dt.datetime.strptime(item['postdate'],'%Y%m%d').date()
                    if date>today:raise ValueError()
                    link=d.canonical(item['link']);dates.append(date);items[link]=date
                except (KeyError,ValueError,TypeError):error='INVALID_POSTDATE_OR_LINK'
            if dates:oldest=min(dates) if oldest is None else min(oldest,min(dates));newest=max(dates) if newest is None else max(newest,max(dates))
            if len(rows)<100 or (oldest and oldest<cut):complete=True;break
            recent=sum(cut7<=v<=today for v in items.values())
            # Already above unchanged 30-post threshold; more pages cannot rescue it.
            if recent>30 or trends[q]['status']!='ok':break
            # Reserve one request for every remaining keyword.
            if budget-blog_calls<=len(supply_order)-qi-1:break
        recent=sum(cut7<=v<=today for v in items.values());previous=sum(cut<=v<cut7 for v in items.values());two=sum(today-dt.timedelta(days=1)<=v<=today for v in items.values())
        blogs[q]={'status':'failed' if error else 'ok' if complete else 'censored' if pages else 'not_requested',
                  'total':total,'recent7d':recent,'previous7d':previous,'supply_change':recent-previous if complete and not error else None,
                  'recent2_calendar_dates':two,'latest_density_proxy':round(two/max(1,len(items)),4),
                  'recent7d_complete':bool(complete or oldest and oldest<cut7) and not error,'previous7d_complete':complete and not error,
                  'oldest_sample_date':str(oldest) if oldest else None,'newest_sample_date':str(newest) if newest else None,
                  'sample':len(items),'pages':pages,'checked':iso(time.time()),'error':error,'posts':{k:str(v) for k,v in items.items()}}
        print('quality',q,'search',trends[q]['diagnosis'],'blog',blogs[q]['status'],pages,flush=True)
    allq=list(dict.fromkeys(x['query'] for p in plans for x in p))
    for q in allq:
        if q not in trends:trends[q]={'status':'not_requested','diagnosis':'priority_budget','points':[],'level':None,'growth':None,'latest_date':'','requested_end':str(end)}
        if q not in blogs:blogs[q]={'status':'not_requested','total':None,'recent7d':None,'previous7d':None,'supply_change':None,'sample':0}
    return trends,blogs,{'requested_queries':ordered,'generated_unique':len(allq),'weekly_context':weekly,'raw':raw,'blog_call_budget':budget}
