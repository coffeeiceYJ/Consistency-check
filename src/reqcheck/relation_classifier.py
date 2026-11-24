"""Native LiSSA classifier adapter. Remote calls pass through the metered client.

SimpleClassifier remains responsible for native yes/no classification. The local
transport preserves uncertain/error decisions so they are never silently treated
as unrelated. No provider credential is passed to Java or the loopback server.
"""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import task_prompts


def validate_relation(value):
    if not isinstance(value,dict) or value.get('decision') not in ('yes','no','uncertain'):
        raise ValueError('Invalid relation decision')
    if not isinstance(value.get('reason'),str) or not value['reason'].strip(): raise ValueError('Missing relation reason')
    return {'decision':value['decision'],'reason':value['reason']}


class LiSSARelationClassifier:
    name='original-lissa-SimpleClassifier/metered-transport'

    def __init__(self, retriever): self.retriever=retriever

    def classify(self, pairs, runner):
        if not pairs: return []
        retriever=self.retriever
        source=Path(__file__).resolve().parents[2]/'bridge/LissaClassifierBridge.java'
        signature=hashlib.sha256(source.read_bytes()+str(retriever.jar.stat().st_mtime_ns).encode()).hexdigest()[:16]
        classes=source.parent.parent/'outputs/bridge'/signature;classes.mkdir(parents=True,exist_ok=True)
        if not (classes/'LissaClassifierBridge.class').is_file():
            proc=subprocess.run([retriever._tool('javac'),'-J-Duser.language=en','-encoding','UTF-8','-cp',str(retriever.jar),'-d',str(classes),str(source)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
            if proc.returncode: raise RuntimeError('Native LiSSA classifier compile failed: '+proc.stderr[-1800:])
        token=secrets.token_hex(24); decisions={}; failures=[]
        expected={(p['source']['id'],p['target']['id']) for p in pairs}
        lock=threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                    self.send_error(403);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<4*1024*1024: raise ValueError('Invalid request size')
                    body=json.loads(self.rfile.read(size))
                    prompt=body['messages'][-1]['content']
                    head,sep,tail=prompt.partition('\nTARGET_JSON\n')
                    if not sep or not head.startswith('SOURCE_JSON\n'): raise ValueError('Invalid classifier transport prompt')
                    a=json.loads(head[len('SOURCE_JSON\n'):]);b=json.loads(tail)
                    key=(a['id'],b['id'])
                    if key not in expected: raise ValueError('Unexpected classification pair')
                    payload={'stage':'relation','requirement_ids':[a['id']],'model_ids':[b['id']],
                             'source':a,'target':b,'task':task_prompts.RELATION}
                    with lock:
                        try:
                            value=runner.run('relation',task_prompts.SYSTEM,payload,validate_relation)
                        except Exception as exc:
                            value={'decision':'uncertain','reason':'Relation call failed; retained for checking'}
                            failures.append({'requirement_id':key[0],'model_id':key[1],'kind':type(exc).__name__})
                        decisions[key]=value
                    # A native no for uncertainty is never consumed as unrelated by the adapter.
                    content=value['decision']
                    envelope={'id':'local-relation','object':'chat.completion','created':0,'model':body.get('model','metered'),
                              'choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],
                              'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
                    data=json.dumps(envelope).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                except Exception:
                    self.send_error(400,'Invalid classification transport')

        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='reqcheck-lissa-') as folder:
                temp=Path(folder);inp=temp/'input.json';out=temp/'output.json'
                data={'gateway':f'http://127.0.0.1:{server.server_port}/v1','local_token':token,
                      'model':'reqcheck-metered','pairs':pairs}
                inp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                proc=subprocess.run([retriever._tool('java'),'-Duser.language=en','-Dfile.encoding=UTF-8','-cp',os.pathsep.join([str(classes),str(retriever.jar)]),
                     'LissaClassifierBridge',str(inp),str(out),str(temp/'cache')],capture_output=True,text=True,encoding='utf-8',errors='replace',
                     timeout=max(120,len(pairs)*(getattr(runner.client,'timeout',120)+10)))
                if proc.returncode or not out.is_file():
                    # Java only sees a random local token. Do not echo provider/private response text.
                    raise RuntimeError('Native LiSSA classifier failed; code '+str(proc.returncode))
                native=json.loads(out.read_text(encoding='utf-8'))
            linked={(x['requirement_id'],x['model_id']):x['linked'] for x in native}
            results=[]
            for a,b in sorted(expected):
                value=decisions.get((a,b))
                if value is None: raise RuntimeError('Native classifier did not call metered transport')
                if linked.get((a,b))!=(value['decision']=='yes'): raise RuntimeError('Native relation result mismatch')
                results.append({'requirement_id':a,'model_id':b,**value,'method':self.name})
            self.failures=failures
            return results
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
