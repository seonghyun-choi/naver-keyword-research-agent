"""Additional conservative merges on typed event signatures, not entity alone."""
import re,collections,hashlib,itertools
from functools import lru_cache
from vendor.event_clusters import consolidate,normalized,conflict

@lru_cache(maxsize=20000)
def signature(title):
    s=normalized(title).replace('3q','3분기').replace('2q','2분기').replace('1q','1분기').replace('4q','4분기')
    for a,b in [('영업익','영업이익'),('순익','순이익'),('매출액','매출'),('하나금융그룹','하나금융'),('우리금융지주','우리금융'),('신한금융지주','신한금융')]:s=s.replace(a,b)
    m=re.match(r'\s*([가-힣a-z][가-힣a-z0-9·&]+)',s);entity=m.group(1) if m else None
    if entity:entity=re.sub(r'(에서는|에서|은|는)$','',entity) if len(entity)>3 else entity
    quarter=next(iter(re.findall(r'([1-4])\s*분기',s)),None)
    year=next(iter(re.findall(r'(20\d{2})년',s)),None)
    metrics={}
    for k in ('매출','영업이익','순이익'):
        m=re.search(k+r'(?:은|는|이|액|도)?\s*(\d+(?:\.\d+)?)\s*(조|억)(?:\s*(\d+(?:\.\d+)?)\s*억)?(?:원)?',s)
        if m:metrics[k]=float(m[1])*(10000 if m[2]=='조' else 1)+(float(m[3]) if m[3] else 0)
    product=next((x for x in ('발행어음','주택담보대출','주담대','영구채','회사채','자사주','과세','배당소득') if x in s),None)
    action='earnings' if quarter and re.search('매출|영업이익|순이익',s) else 'launch' if re.search('출시|선보|선봬',s) else 'acquisition' if '인수' in s else 'tax' if '과세' in s else None
    obj=None
    if action=='acquisition':
        m=re.search(r'([가-힣a-z][가-힣a-z0-9]{2,20})(?:을|를)?\s*(?:공동\s*)?인수',s);obj=m[1] if m else None
    stage=next((x for x in ('우선협상','추진','검토','승인','완료','계약') if x in s),None)
    return {'entity':entity,'action':action,'object':obj or product,'quarter':quarter,'year':year,'cumulative':'누적' in s,
            'scope':'별도' if '별도' in s else '연결' if '연결' in s else None,'forecast':bool(re.search('전망|예상|추정|목표',s)),
            'metrics':metrics,'stage':stage,'text':s}

def same_event(a,b):
    x,y=signature(a['label']),signature(b['label'])
    if not x['entity'] or x['entity']!=y['entity']:return False,'entity_diff_or_unknown'
    if not x['action'] or x['action']!=y['action']:return False,'action_diff_or_unknown'
    if abs(a['published']-b['published'])>86400:return False,'publication_gap_over_24h'
    if x['forecast']!=y['forecast']:return False,'forecast_vs_actual'
    for k in ('year','scope','stage'):
        if x[k] and y[k] and x[k]!=y[k]:return False,'different_'+k
    if x['action']=='earnings':
        if ('달러' in x['text'])!=('달러' in y['text']):return False,'different_currency'
        if x['quarter']!=y['quarter'] or x['cumulative']!=y['cumulative']:return False,'different_earnings_period'
        common=x['metrics'].keys()&y['metrics'].keys()
        if any(x['metrics'][k]!=y['metrics'][k] for k in common):return False,'different_typed_amount'
        # A matching number must refer to the same financial metric, not just the same unit.
        if not common:return False,'no_matching_financial_metric'
        if re.search('흑자|적자',x['text']) and re.search('흑자|적자',y['text']) and bool(re.search('적자',x['text']))!=bool(re.search('적자',y['text'])):return False,'profit_direction_conflict'
        return True,'same_entity_quarter_scope_and_metric_value'
    if not x['object'] or x['object']!=y['object']:return False,'object_diff_or_unknown'
    bad=conflict(x['text'],y['text'])
    if bad:return False,bad
    if x['action']=='acquisition' and x['stage']!=y['stage']:return False,'acquisition_stage_unresolved'
    if x['action']=='tax':return False,'policy_detail_requires_review'
    if x['action']=='launch' and x['object'] not in ('발행어음','영구채'):return False,'product_identity_requires_review'
    return True,'same_entity_action_object_nonconflicting_context'

def improved_consolidate(events):
    old,edges,near=consolidate(events);byid={e['id']:e for e in events}
    groups={g['cluster_id']:list(g['event_ids']) for g in old};owner={i:k for k,ids in groups.items() for i in ids}
    buckets=collections.defaultdict(list)
    for e in events:
        s=signature(e['label'])
        if s['entity'] and s['action']:buckets[(s['entity'],s['action'])].append(e['id'])
    accepted=[];rejected=[]
    for key in sorted(buckets):
        for a,b in itertools.combinations(buckets[key],2):
            ga,gb=owner[a],owner[b]
            if ga==gb:continue
            ok,why=same_event(byid[a],byid[b])
            record={'a':a,'b':b,'reason':why}
            if not ok:
                if len(rejected)<300:rejected.append(record)
                continue
            if not all(same_event(byid[i],byid[j])[0] for i in groups[ga] for j in groups[gb]):
                if len(rejected)<300:rejected.append(dict(record,reason='complete_link_guard'))
                continue
            accepted.append(record);groups[ga]+=groups.pop(gb)
            for i in groups[ga]:owner[i]=ga
    result=[{'cluster_id':hashlib.sha256('|'.join(sorted(ids)).encode()).hexdigest()[:20],'event_ids':sorted(ids)} for ids in groups.values()]
    return result,edges+accepted,near,{'before_events':len(old),'after_events':len(result),'additional_merges':accepted,'semantic_rejections':rejected}
