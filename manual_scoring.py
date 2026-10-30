"""Transparent relative indices; no absolute search-volume inference."""
import math

def news_metrics(articles, now):
    bins=[0,0,0]
    for a in articles:
        age=(now-a['published'])/3600
        if 0<=age<=48:bins[0 if age<6 else 1 if age<24 else 2]+=1
    latest=max(a['published'] for a in articles)
    fresh=max(0,1-(now-latest)/172800)
    topicality=min(1,math.log1p(bins[0]+.55*bins[1]+.25*bins[2])/math.log(21))
    return {'bins':bins,'topicality':round(topicality*100,2),'freshness':fresh,
            'first_published':min(a['published'] for a in articles),'latest_published':latest}

def evaluate(news, trend, blog, publishers, event_day):
    reasons=[];demand=None;rise=None;dso=None;score=None;parts={}
    if trend['status']=='ok':
        # Normalize WITHIN the query's own history, never across separate API batches.
        demand=trend['level'];rise=max(0,min(1,trend['growth']/1.0))
    else:reasons.append('검색 데이터 부족: '+trend['status'])
    if trend.get('latest_date') and trend['latest_date']<event_day:reasons.append('일별 검색 지수가 사건 발생일보다 이전임')
    if blog['status']!='ok':reasons.append('블로그 공급 측정 불완전: '+blog['status'])
    scarcity=None
    if blog['status'] in ('ok','censored'):
        # Seven calendar dates and total are separate proxies; never an exact count.
        competition=.8*min(1,math.log1p(blog['recent7d'])/math.log(101))+.2*min(1,math.log1p(blog['total'])/math.log(100001))
        scarcity=max(0,1-competition)
        if blog['recent7d']>30:reasons.append('최근 블로그 공급 많음')
        if demand is not None:
            dso=100*(.65*demand+.35*rise)*scarcity
            parts={'수요·공급':.40*dso,'검색 상승':20*rise,'화제성':.15*news['topicality'],
                   '신선도':10*news['freshness'],'경제 적합성':10,'언론사 확산':5*min(1,publishers/5)}
            score=round(sum(parts.values()),2)
    if demand is not None and (demand<.3 or trend['growth']<.15):reasons.append('확인된 상대 관심 상승이 충분하지 않음')
    if publishers<2:reasons.append('서로 다른 언론사 근거 부족')
    if score is not None and score<65:reasons.append('추천 기준점 65 미달')
    if dso is not None and dso<40:reasons.append('수요·공급 기회 지수 40 미달')
    qualified=score is not None and not reasons
    # This is editorial watch priority only, never a surrogate Opportunity score.
    watch=round(.6*news['topicality']+25*news['freshness']+15*min(1,publishers/5),2)
    return {'demand_supply':round(dso,2) if dso is not None else None,'score':score,
            'parts':{k:round(v,2) for k,v in parts.items()},'reasons':reasons,
            'decision':'지금 작성' if qualified else '관심 후보','qualified':qualified,
            'confidence':'보통 (일별·검색표본)' if qualified else '낮음',
            'watch_priority':watch,'scarcity':scarcity,'relative_interest':demand}
