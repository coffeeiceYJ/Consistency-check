"""Real LiSSA retrieval with replaceable vector creation (offline TF-IDF by default)."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]

def tokens(text):
    text=re.sub(r'([a-z])([A-Z])',r'\1 \2',text).lower()
    words=re.findall(r'[a-z0-9_]+', text)
    # Chinese unigrams and bigrams give a transparent lexical baseline, not semantic embeddings.
    for run in re.findall(r'[\u3400-\u9fff]+',text):
        words.extend(run); words.extend(run[i:i+2] for i in range(len(run)-1))
    return words

def tfidf(texts):
    counts=[Counter(tokens(t)) for t in texts]
    vocab=sorted(set().union(*(c.keys() for c in counts)))
    if not vocab: return [[0.0] for _ in texts]
    df=Counter(w for c in counts for w in c)
    return [[(1+math.log(c[w]))*(1+math.log((1+len(texts))/(1+df[w]))) if c[w] else 0.0 for w in vocab] for c in counts]

class LiSSARetriever:
    name='lissa-cosine/local-tfidf'
    def __init__(self, java_home=None, jar=None, embedder=None, timeout=120):
        self.java_home=Path(java_home) if java_home else None
        import os
        configured=jar or os.environ.get('LISSA_JAR')
        candidates=[ROOT/'vendor/lissa/target/lissa-1.1.0-SNAPSHOT-jar-with-dependencies.jar',
                    ROOT.parent/'references/lissa/target/lissa-1.1.0-SNAPSHOT-jar-with-dependencies.jar']
        self.jar=Path(configured) if configured else next((p for p in candidates if p.is_file()),candidates[0])
        self.embedder=embedder
        self.timeout=timeout
        self.provenance={'retrieval':self.name,'lissa_scope':'Element + TargetElementStore + CosineSimilarity; new checker, not original classifier'}

    def _tool(self,name):
        candidates=[self.java_home/'bin'/f'{name}.exe'] if self.java_home else []
        found=shutil.which(name)
        if found: candidates.append(Path(found))
        candidates.append(Path('D:/PyCharm Community Edition 2025.1.2/jbr/bin')/f'{name}.exe')
        for candidate in candidates:
            if candidate.is_file():
                version=subprocess.run([str(candidate),'-version'],capture_output=True,text=True,timeout=10)
                if re.search(r'(?:version\s+"|javac\s+)(?:2[1-9]|[3-9]\d)\.',version.stdout+version.stderr): return str(candidate)
        raise RuntimeError(f'Java 21+ {name} not found; set --java-home')

    def retrieve(self,request):
        if not self.jar.is_file(): raise RuntimeError('Build LiSSA first: scripts/build_lissa.ps1')
        source=ROOT/'bridge/LissaBridge.java'
        signature=hashlib.sha256(source.read_bytes()+str(self.jar.stat().st_mtime_ns).encode()).hexdigest()[:16]
        classes=ROOT/'outputs/bridge'/signature
        classes.mkdir(parents=True,exist_ok=True)
        if not (classes/'LissaBridge.class').exists():
            proc=subprocess.run([self._tool('javac'),'-encoding','UTF-8','-cp',str(self.jar),'-d',str(classes),str(source)],capture_output=True,text=True,timeout=self.timeout)
            if proc.returncode: raise RuntimeError('LiSSA bridge compile failed: '+proc.stderr[-2000:])
        artifacts=request.requirements+request.models
        vectors=self.embedder([a.text for a in artifacts]) if self.embedder else tfidf([a.text for a in artifacts])
        if len(vectors)!=len(artifacts) or not vectors or not vectors[0]: raise ValueError('Invalid embedding count or dimension')
        dim=len(vectors[0])
        if any(len(v)!=dim or any(not math.isfinite(x) for x in v) for v in vectors): raise ValueError('Invalid embedding vectors')
        top_k=request.options.get('top_k',4)
        if type(top_k)!=int or top_k<1: raise ValueError('top_k must be positive integer')
        payload={'top_k':top_k,'sources':[],'targets':[]}
        for i,a in enumerate(artifacts):
            payload['sources' if i<len(request.requirements) else 'targets'].append({'id':a.id,'text':a.text,'vector':vectors[i]})
        import os
        with tempfile.TemporaryDirectory(prefix='reqcheck-') as temp:
            inp=Path(temp)/'input.json'; out=Path(temp)/'links.json'
            inp.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
            proc=subprocess.run([self._tool('java'),'-cp',os.pathsep.join([str(classes),str(self.jar)]),'LissaBridge',str(inp),str(out)],capture_output=True,text=True,timeout=self.timeout)
            if proc.returncode or not out.exists(): raise RuntimeError('LiSSA bridge execution failed: '+proc.stderr[-1500:])
            links=json.loads(out.read_text(encoding='utf-8'))
        for link in links: link.update(method=self.name, status='候选关联，尚非需求满足结论')
        self.provenance['vectorizer']=getattr(self.embedder,'name','local-tfidf')
        self.provenance['jar_sha256']=hashlib.sha256(self.jar.read_bytes()).hexdigest()
        return links
