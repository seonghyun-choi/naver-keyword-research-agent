"""Versioned batch scores. Missing demand is never replaced with news."""
import datetime as dt, math
KST=dt.timezone(dt.timedelta(hours=9))

def news_metrics(stories,articles,now,coverage):
    # Publication-time histogram, NOT observation-time realtime Momentum.
    bins=[sum(now-(i+1)*3600<=s['published']<now-i*3600 for s in stories) for i in range(12)]
    sources=len({a['publisher'] for a in articles});n=len(stories)
    active=sum(x>0 for x in bins[:6]);current=sum(bins[:2]);previous=sum(bins[2:4])
    ready=coverage and n>=4 and active>=2 and sources>=2
    growth=(current-previous)/(previous+1) if ready else None
    velocity=current/120 if ready else None
    acceleration=(current-previous)/(120*120) if ready else None
    age=(now-min(s['published'] for s in stories))/3600
    recent=sum(bins[:6]);old=sum(bins[6:])
    recency=recent/max(1,n)
    news=min(1,max(0,.5+.25*growth)) if growth is not None else 0
    early=max(0,1-age/12)*recency
    return {'bins_newest_first':bins,'unique_stories':n,'unique_sources':sources,'article_count':len(articles),
            'current_2h':current,'previous_2h':previous,'recent_6h':recent,'previous_6h':old,'active_hour_bins':active,
            'growth':growth,'velocity':velocity,'acceleration':acceleration,'news_momentum':None,
            'basis':'발행시각 기준 최근 2시간 대 이전 2시간; 실시간 관측 Momentum 아님',
            'first_published':min(s['published'] for s in stories),'age_hours':age,'ready':ready,
            'news_component':news,'early_component':early,'source_component':min(1,sources/5)}

def evaluate(m,trend,blog,cfg):
    reasons=[];score=None
    if not m['ready']:reasons.append('뉴스 시계열 표본 또는 수집 시간범위 부족')
    if m['current_2h']<2:reasons.append('최근 2시간 보도 지속성 부족')
    if m['growth'] is not None and m['growth']<=0:reasons.append('최근 뉴스 증가 미확인')
    blog_ok=bool(blog and blog.get('status')=='ok' and blog.get('sample',0)>0)
    if not blog_ok:reasons.append('블로그 측정 실패·상한 도달·표본 없음')
    recent=(blog or {}).get('recent7d',0)
    if recent>cfg['max_blog_recent7d']:reasons.append('최근 7일 블로그 공급 과다')
    scarcity=max(0,1-recent/cfg['max_blog_recent7d']) if blog_ok else 0
    # Own-series growth only; ratios from different queries are never absolute demand.
    search_ok=bool(trend and trend.get('status')=='ok' and trend.get('growth',0)>0)
    event_day=dt.datetime.fromtimestamp(m['first_published'],KST).date().isoformat()
    aligns=bool(trend and (trend.get('latest_date') or '')>=event_day)
    if not search_ok:reasons.append('일별 검색 상승 근거 없음')
    if not aligns:reasons.append('일별 검색 데이터가 사건보다 이전')
    demand=min(1,max(0,(trend or {}).get('growth') or 0)) if search_ok else None
    confidence=.9 if m['ready'] and blog_ok and search_ok and aligns else .6 if m['ready'] and blog_ok else .3
    parts={'search_supply_gap':None if demand is None else round(55*demand*scarcity,2),
           'news':round(15*m['news_component'],2),'early':round(15*m['early_component'],2),
           'sources':round(10*m['source_component'],2),'relevance':5}
    if demand is not None and aligns and blog_ok and m['ready']:
        score=round(sum(parts.values())*confidence,2)
    watch_parts={'news':round(30*m['news_component'],2),'early':round(20*m['early_component'],2),
                 'sources':round(15*m['source_component'],2),'low_blog_supply':round(25*scarcity,2),'relevance':10}
    watch=round(sum(watch_parts.values()),2)
    gates=m['ready'] and m['current_2h']>=2 and m['growth'] is not None and m['growth']>0 and blog_ok and recent<=cfg['max_blog_recent7d']
    decision='지금 작성' if gates and search_ok and aligns and score is not None and score>=cfg['write_threshold'] else '관찰' if gates and watch>=cfg['watch_threshold'] else '패스'
    if decision=='패스' and not reasons:reasons.append('고정 추천 점수 기준 미달')
    return {'decision':decision,'opportunity_score':score,'observation_priority':watch,'confidence':confidence,
            'score_components':parts,'observation_components':watch_parts,'reasons':reasons,
            'status':'VERIFIED OPPORTUNITY' if decision=='지금 작성' else 'UNVERIFIED CANDIDATE',
            'score_note':'점수는 유입 확률이 아님. 관찰 우선순위는 Opportunity Score와 비교 불가.'}
