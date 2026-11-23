"""Conservative activity CFG subset. Rules are supplied explicitly, not inferred from NL."""
from collections import deque
import re

class UnsupportedActivity(ValueError): pass

def parse_activity(artifact):
    nodes=[]; edges={}; rows=[]
    for i,line in enumerate(artifact.text.splitlines(),artifact.start_line):
        s=line.strip()
        if not s or s.startswith("'") or s.startswith('@startuml') or s=='@enduml' or s.startswith('title '): continue
        rows.append((i,s))
    def node(kind,label,line):
        n=len(nodes); nodes.append({'kind':kind,'label':label,'line':line}); edges[n]=[]; return n
    def connect(incoming,target):
        for src,guard in incoming: edges[src].append((target,guard))
    index=0
    def sequence(incoming,depth=0):
        nonlocal index
        if depth>50: raise UnsupportedActivity('活动图嵌套超过 50 层')
        while index<len(rows):
            line,s=rows[index]
            if s.startswith('else') or s=='endif': break
            index+=1
            if s in ('start','stop','end'):
                n=node('start' if s=='start' else 'end',s,line); connect(incoming,n)
                incoming=[(n,None)] if s=='start' else []
            elif s.startswith(':') and s.endswith(';'):
                n=node('action',s[1:-1].strip(),line); connect(incoming,n); incoming=[(n,None)]
            elif m:=re.fullmatch(r'if\s*\((.+)\)\s*then\s*\((yes|true|是)\)',s,re.I):
                cond=m.group(1); n=node('decision',cond,line); connect(incoming,n)
                yes=sequence([(n,(cond,True))],depth+1)
                if index>=len(rows) or not re.fullmatch(r'else\s*\((no|false|否)\)',rows[index][1],re.I):
                    raise UnsupportedActivity(f'第 {line} 行：仅支持完整 if/then(yes)/else(no)/endif')
                index+=1
                no=sequence([(n,(cond,False))],depth+1)
                if index>=len(rows) or rows[index][1]!='endif': raise UnsupportedActivity('缺少 endif')
                index+=1; incoming=yes+no
            else:
                raise UnsupportedActivity(f'第 {line} 行未支持语法：{s[:120]}')
        return incoming
    sequence([])
    if index!=len(rows): raise UnsupportedActivity('未配对的 else/endif')
    if not nodes or nodes[0]['kind']!='start' or sum(n['kind']=='start' for n in nodes)!=1: raise UnsupportedActivity('需要唯一 start')
    conditions=[n['label'] for n in nodes if n['kind']=='decision']
    if len(conditions)!=len(set(conditions)): raise UnsupportedActivity('重复条件可能随动作变化，当前不能判断，需语义模型')
    if any(not edges[i] and n['kind']!='end' for i,n in enumerate(nodes)): raise UnsupportedActivity('存在未终止路径')
    return nodes,edges

def evidence(a,start,end=None):
    end=start if end is None else end
    lines=a.text.splitlines()
    return {'artifact_id':a.id,'start_line':start,'end_line':end,
            'quote':'\n'.join(lines[start-a.start_line:end-a.start_line+1]),
            'source':{'file':a.file,'start_line':start,'end_line':end},'verified':True}

def check_before(rule,requirement,model):
    if model.diagram_type!='activity': raise UnsupportedActivity('before 规则目前只支持活动图')
    if rule.get('kind')!='before' or not isinstance(rule.get('before'),str) or not isinstance(rule.get('after'),str):
        raise UnsupportedActivity('无效 before 规则')
    nodes,edges=parse_activity(model)
    before,after=rule['before'],rule['after']
    gate=rule.get('required_gate')
    if gate is not None and (not isinstance(gate,dict) or type(gate.get('value'))!=bool or not isinstance(gate.get('condition'),str)):
        raise UnsupportedActivity('required_gate 需要 condition 和布尔 value')
    if not any(n['label']==before and n['kind']=='action' for n in nodes) or not any(n['label']==after and n['kind']=='action' for n in nodes):
        return {'verdict':'无法判断','summary':'未找到规则指定的动作，不能据此确认遗漏。','evidence':[evidence(requirement,requirement.start_line,requirement.end_line)],'path':[]}
    # Track assignments to repeated predicates; contradictory branches are not traversed.
    queue=deque([(0,False,False,(),[])])
    seen=set(); checked=0; reached=False
    while queue:
        i,visited,gate_seen,assignments,path=queue.popleft()
        state=(i,visited,gate_seen,assignments)
        if state in seen: continue
        seen.add(state); checked+=1
        if checked>5000: raise UnsupportedActivity('路径状态超过 5000，检查不完整')
        n=nodes[i]; path=path+[i]
        if n['kind']=='action' and n['label']==after:
            reached=True
            if not visited or (gate and not gate_seen):
                return {'verdict':'存在冲突','summary':f'存在到达“{after}”的路径，未先经过“{before}”'+('及指定成功条件。' if gate else '。'),
                        'evidence':[evidence(requirement,requirement.start_line,requirement.end_line)]+[evidence(model,nodes[j]['line']) for j in path],
                        'path':[nodes[j] for j in path]}
        if n['kind']=='action' and n['label']==before:
            visited=True
            gate_seen=False  # A new attempt invalidates a success condition from an earlier attempt.
        for target,guard in edges[i]:
            facts=dict(assignments); passed=gate_seen
            if guard:
                cond,val=guard
                if cond in facts and facts[cond]!=val: continue
                facts[cond]=val
                if gate and visited and cond==gate['condition'] and val==gate['value']: passed=True
            queue.append((target,visited,passed,tuple(sorted(facts.items())),path))
    return {'verdict':'一致' if reached else '无法判断',
            'summary':'支持的活动图子集内，所有到达目标动作的路径均满足这条显式规则。' if reached else '目标动作不可达，不能确认需求满足。',
            'evidence':[evidence(requirement,requirement.start_line,requirement.end_line),evidence(model,model.start_line,model.end_line)],'path':[]}
