"""Injected OpenAI-compatible HTTPS clients; secrets never enter result objects."""
import json
import os
import urllib.error
import urllib.request

class ModelError(RuntimeError):
    def __init__(self,message,kind='model_error',http_status=None,finish_reason=None):
        super().__init__(message)
        allowed={'model_error','http_error','timeout','transport_error','invalid_json','blank_content',
                 'truncated_output','incomplete_output','response_schema','response_too_large'}
        self.diagnostic={'failure_kind':kind if kind in allowed else 'model_error'}
        if type(http_status) is int: self.diagnostic['http_status']=http_status
        if finish_reason in ('length','content_filter','tool_calls','stop'):
            self.diagnostic['finish_reason']=finish_reason

class JSONModelClient:
    name='openai-compatible-json'
    def __init__(self, endpoint, model, key=None, key_env='DEEPSEEK_API_KEY', timeout=120, max_tokens=6000, thinking_mode='auto'):
        from urllib.parse import urlparse
        url=urlparse(endpoint)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Model endpoint must be an HTTPS URL without credentials, query or fragment')
        self.endpoint=endpoint
        self.model=model
        if thinking_mode not in ('auto','enabled','disabled'):
            raise ValueError('thinking_mode must be auto, enabled or disabled')
        # DeepSeek currently enables thinking by default, sharing the generation
        # budget with the final JSON. Other providers keep their own defaults.
        self.thinking_mode=('disabled' if url.hostname=='api.deepseek.com' else None) if thinking_mode=='auto' else thinking_mode
        self._key=key or os.environ.get(key_env)
        if not self._key: raise ValueError(f'Missing model credential in {key_env}')
        if type(timeout) not in (int,float) or not 0 < timeout <= 3600:
            raise ValueError('timeout must be a number in (0, 3600] seconds')
        self.timeout=timeout
        if type(max_tokens) is not int or max_tokens < 1: raise ValueError('max_tokens must be a positive integer')
        self.max_tokens=max_tokens
        self.calls=0
        self.usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
        self.last_response_info={}
        self.provenance={'model':model,'client':self.name,'mode':'live','max_output_tokens':max_tokens,'timeout_seconds':timeout,
                         'thinking_mode':self.thinking_mode or 'provider-default'}

    def _request(self,payload):
        request=urllib.request.Request(self.endpoint,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+self._key})
        self.calls+=1
        try:
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024: raise ModelError('Model response exceeds 8 MiB','response_too_large')
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Do not echo response bodies, request headers or credentials.
            raise ModelError(f'Model HTTP status {exc.code}','http_error',http_status=exc.code) from None
        except TimeoutError:
            raise ModelError('Model request timed out','timeout') from None
        except urllib.error.URLError as exc:
            raise ModelError('Model transport failed','timeout' if isinstance(exc.reason,TimeoutError) else 'transport_error') from None
        except OSError:
            raise ModelError('Model transport failed','transport_error') from None
        except json.JSONDecodeError:
            raise ModelError('Model returned invalid JSON','invalid_json') from None

    def complete(self,system,payload):
        self.last_response_info={}
        body={'model':self.model,'messages':[{'role':'system','content':system},
                {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
                'response_format':{'type':'json_object'},'temperature':0,'stream':False,'max_tokens':self.max_tokens}
        if self.thinking_mode: body['thinking']={'type':self.thinking_mode}
        response=self._request(body)
        try:
            if not isinstance(response,dict): raise ModelError('Invalid response envelope','response_schema')
            self.last_response_info['usage_reported']=isinstance(response.get('usage'),dict)
            for k in self.usage: self.usage[k]+=int(response.get('usage',{}).get(k,0))
            choice=response['choices'][0]
            message=choice.get('message',{})
            if isinstance(message,dict):
                # Store sizes only, never the private reasoning text.
                for field in ('content','reasoning_content'):
                    value=message.get(field)
                    self.last_response_info['content_chars' if field=='content' else 'reasoning_chars']=len(value) if isinstance(value,str) else 0
            details=response.get('usage',{}).get('completion_tokens_details',{})
            if isinstance(details,dict) and type(details.get('reasoning_tokens')) is int:
                self.last_response_info['reasoning_tokens']=details['reasoning_tokens']
            reason=choice.get('finish_reason')
            self.last_response_info['finish_reason']=reason if reason in (None,'stop','length','content_filter','tool_calls') else 'other'
            if reason not in (None,'stop'):
                raise ModelError('Model response incomplete','truncated_output' if reason=='length' else 'incomplete_output',finish_reason=reason)
            content=choice['message']['content']
            if not isinstance(content,str) or not content.strip(): raise ModelError('Empty model content','blank_content')
            try: result=json.loads(content)
            except json.JSONDecodeError: raise ModelError('Invalid model JSON content','invalid_json') from None
            if not isinstance(result,dict): raise ModelError('Expected JSON object','response_schema')
            return result
        except (KeyError,IndexError,TypeError,ValueError,AttributeError): raise ModelError('Invalid structured model response','response_schema') from None

class HTTPEmbedder(JSONModelClient):
    name='remote-embeddings'
    def __call__(self,texts):
        result=self._request({'model':self.model,'input':texts})
        try:
            rows=sorted(result['data'],key=lambda r:r['index'])
            if [r['index'] for r in rows]!=list(range(len(texts))): raise ValueError()
            return [r['embedding'] for r in rows]
        except (KeyError,TypeError,ValueError): raise ModelError('Invalid embedding response') from None
