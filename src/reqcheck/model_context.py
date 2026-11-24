"""Source-derived structure hints. No business answers or generated test rules."""
from .activity import parse_activity, UnsupportedActivity


def activity_context(model,max_paths=32,max_nodes=256):
    try:
        nodes,edges=parse_activity(model)
        if len(nodes)>max_nodes:raise UnsupportedActivity('控制流节点超过辅助分析预算')
    except (ValueError,RecursionError) as exc:
        return {'model_id':model.id,'status':'unsupported','reason':str(exc),
                'scope':'辅助解析不可用，仍提供完整源码；不能声称已做完整路径枚举。'}
    paths=[];pending=[(0,[],[],set())]
    while pending:
        i,prefix,guards,visited=pending.pop()
        if i in visited:
            return {'model_id':model.id,'status':'unsupported','reason':'辅助路径分析不支持循环'}
        path=prefix+[{'line':nodes[i]['line'],'kind':nodes[i]['kind'],'label':nodes[i]['label']}]
        if not edges[i]:
            paths.append({'id':model.id+'.path'+str(len(paths)+1),'guards':guards,'steps':path})
            if len(paths)>=max_paths and pending:
                return {'model_id':model.id,'status':'partial','paths':paths,'reason':'完整路径数超过辅助枚举预算'}
        for target,guard in reversed(edges[i]):
            conditions=guards+[{'condition':guard[0],'value':guard[1]}] if guard else guards
            pending.append((target,path,conditions,visited|{i}))
    return {'model_id':model.id,'status':'complete-supported-subset','paths':paths,
            'scope':'支持的顺序及完整if/else子集；只确认控制流，不判断动作的业务语义。'}
