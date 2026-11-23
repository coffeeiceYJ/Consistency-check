"""File adapter. Requirement paragraphs stay intact to preserve conditions."""
import hashlib
import json
from pathlib import Path
import re
import uuid
from .contracts import Artifact, CheckRequest

TYPES = {'class', 'activity', 'state', 'sequence', 'system_sequence', 'usecase', 'unknown'}

def detect_type(text):
    if re.search(r'^\s*(?:abstract\s+)?(?:class|interface|enum)\b', text, re.M): return 'class'
    if re.search(r'^\s*(?:state\b|\[\*\]\s*[-.]+>)', text, re.M): return 'state'
    if re.search(r'^\s*(?:start\s*$|:[^\n]*;)', text, re.M): return 'activity'
    if re.search(r'^\s*(?:actor|usecase)\b', text, re.M) and not re.search(r'^\s*participant\b', text, re.M): return 'usecase'
    if re.search(r'^\s*(?:participant\b|\w+\s*-+>[^\n]*:)', text, re.M): return 'sequence'
    return 'unknown'

def load_request(manifest: str | Path) -> CheckRequest:
    path = Path(manifest).resolve()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict): raise ValueError('Manifest must be a JSON object')
    if data.get('schema_version', '1.0') != '1.0': raise ValueError('Unsupported schema_version')
    result = CheckRequest([], [], data.get('rules', []), data.get('options', {}), uuid.uuid4().hex)
    for kind in ('requirements', 'models'):
        if not isinstance(data.get(kind),list): raise ValueError(f'{kind} must be an array')
        for spec in data.get(kind, []):
            if not isinstance(spec,dict) or not isinstance(spec.get('path'),str): raise ValueError(f'Invalid {kind} entry')
            if 'id' in spec and (not isinstance(spec['id'],str) or not spec['id'].strip()): raise ValueError('Artifact ID must be a nonempty string')
            if 'responsibility' in spec and not isinstance(spec['responsibility'],str): raise ValueError('Invalid responsibility')
            if 'preserve_ids' in spec and type(spec['preserve_ids']) is not bool: raise ValueError('Invalid preserve_ids')
            file = (path.parent / spec['path']).resolve()
            if file.suffix.lower() not in ({'.md', '.txt'} if kind == 'requirements' else {'.puml'}):
                raise ValueError(f'Unsupported file type: {file}')
            text = file.read_text(encoding='utf-8-sig')
            lines = text.splitlines()
            prefix = spec.get('id') or ('R-' if kind == 'requirements' else 'M-') + hashlib.sha256(str(file).encode()).hexdigest()[:12]
            if kind == 'requirements':
                groups = []
                start = None
                for i, line in enumerate(lines + ['']):
                    if line.strip() and start is None: start = i
                    if not line.strip() and start is not None:
                        groups.append((start, i)); start = None
                for n, (a, b) in enumerate(groups, 1):
                    raw = '\n'.join(lines[a:b])
                    # Keep each whole paragraph; finer obligation splitting belongs to the checker.
                    artifact_id = prefix if len(groups)==1 else f'{prefix}.{n}'
                    if spec.get('preserve_ids'):
                        match = re.match(r'^([A-Za-z][A-Za-z0-9_-]*-\d+)\s*[:：]', raw)
                        if not match: raise ValueError(f'Missing explicit requirement ID at {file}:{a+1}')
                        artifact_id = match.group(1)
                    result.requirements.append(Artifact(artifact_id, raw, str(file), a+1, b))
            else:
                starts = [i for i,l in enumerate(lines) if re.match(r'^\s*@startuml\b', l)]
                ends = [i for i,l in enumerate(lines) if re.match(r'^\s*@enduml\b', l)]
                if not starts or len(starts)!=len(ends): raise ValueError(f'Unbalanced PlantUML blocks: {file}')
                # Preserve the block-only contract by rejecting global preprocessing outside it.
                covered={i for a,b in zip(starts,ends) for i in range(a,b+1)}
                if any(i not in covered and l.strip().startswith('!') for i,l in enumerate(lines)):
                    raise ValueError(f'Preprocessor directives outside diagram blocks are unsupported: {file}')
                for n, (a,b) in enumerate(zip(starts,ends), 1):
                    if b<=a or (n<len(starts) and b>=starts[n]): raise ValueError(f'Nested PlantUML blocks: {file}')
                    raw='\n'.join(lines[a:b+1])
                    diagram_type=spec.get('diagram_type') or detect_type(raw)
                    if not isinstance(diagram_type,str) or diagram_type not in TYPES: raise ValueError('Invalid diagram_type')
                    result.models.append(Artifact(prefix if len(starts)==1 else f'{prefix}.{n}',raw,str(file),a+1,b+1,diagram_type,spec.get('responsibility','')))
    artifacts=result.requirements+result.models
    if not result.requirements or not result.models: raise ValueError('At least one requirement and one diagram are required')
    ids=[a.id for a in artifacts]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate artifact IDs')
    if any(not a.text.strip() for a in artifacts): raise ValueError('Empty artifact')
    if not isinstance(result.rules,list) or not isinstance(result.options,dict): raise ValueError('Invalid rules/options')
    return result
