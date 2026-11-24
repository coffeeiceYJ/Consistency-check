"""Metered, validated model calls. Cache data never contains credentials."""
import hashlib
import json
import time
from pathlib import Path
from .model_client import ModelError


class StageRunner:
    def __init__(self, client, options, progress=None, cancelled=None, cache_dir=None):
        self.client=client; self.options=options; self.records=[]; self.calls=0
        self.progress=progress or (lambda e:None); self.cancelled=cancelled or (lambda:False)
        self.cache=Path(cache_dir) if cache_dir else None
        self.max_calls=options.get('max_calls',100)
        self.max_chars=options.get('max_context_chars',100000)
        self.stage_call_ceiling={}
        if type(self.max_calls) is not int or self.max_calls<0 or type(self.max_chars) is not int or self.max_chars<1000:
            raise ValueError('Invalid call/context limits')

    def run(self, stage, system, payload, validate):
        if self.cancelled(): raise RuntimeError('Check cancelled')
        raw=json.dumps({'system':system,'payload':payload,'provider':getattr(self.client,'provenance',{}),
                        'endpoint':getattr(self.client,'endpoint','injected')},sort_keys=True,ensure_ascii=False)
        if len(system)+len(json.dumps(payload,ensure_ascii=False))>self.max_chars:
            raise RuntimeError('Context budget exceeded; source was not truncated')
        key=hashlib.sha256(raw.encode()).hexdigest()
        path=self.cache/(key+'.json') if self.cache else None
        record={'call':len(self.records)+1,'stage':stage,'input_chars':len(raw),'cache_hit':False,
                'requirement_ids':payload.get('requirement_ids',[]),'model_ids':payload.get('model_ids',[])}
        self.records.append(record)
        if path and path.is_file():
            try:
                value=json.loads(path.read_text(encoding='utf-8'))
                result=validate(value)
                if isinstance(result,dict) and result.get('errors'): raise ValueError('Cached response no longer validates')
                record.update(status='completed',cache_hit=True,usage_delta={})
                self.progress({'phase':'cache_hit','stage':stage,'call':record['call']})
                return result
            except (ValueError,KeyError,TypeError,OSError): pass
        if self.calls>=min(self.max_calls,self.stage_call_ceiling.get(stage,self.max_calls)):
            record.update(status='budget_exceeded',usage_delta={})
            raise RuntimeError('Model call budget exhausted')
        self.calls+=1; start=time.monotonic(); before=dict(getattr(self.client,'usage',{}))
        self.progress({'phase':'model_check','stage':stage,'call':record['call']})
        try:
            response=self.client.complete(system,payload)
            if self.options.get('debug_artifacts'): record['raw_response']=response
            result=validate(response)
            record['status']='partial' if isinstance(result,dict) and result.get('errors') else 'completed'
            if record['status']=='partial' or isinstance(result,dict) and result.get('advisory_errors'): record['raw_response']=response
            # Partial results remain in run evidence; do not make them persistent successes.
            if path and record['status']=='completed' and not (isinstance(result,dict) and result.get('advisory_errors')):
                path.parent.mkdir(parents=True,exist_ok=True)
                temp=path.with_suffix('.tmp')
                temp.write_text(json.dumps(response,ensure_ascii=False),encoding='utf-8'); temp.replace(path)
            return result
        except Exception as exc:
            record['status']='failed'
            if 'response' in locals(): record['raw_response']=response
            record['failure_kind']=exc.diagnostic.get('failure_kind') if isinstance(exc,ModelError) else type(exc).__name__
            raise
        finally:
            record['elapsed_seconds']=round(time.monotonic()-start,3)
            record['usage_delta']={k:v-before.get(k,0) for k,v in getattr(self.client,'usage',{}).items()}
            self.progress({'phase':'model_result','stage':stage,'call':record['call'],'status':record['status']})
