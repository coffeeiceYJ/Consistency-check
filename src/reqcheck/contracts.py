from dataclasses import dataclass, field
from typing import Protocol

COVERAGE = ('完全覆盖', '部分覆盖', '未发现覆盖', '不适用')
CONSISTENCY = ('一致', '存在冲突', '无法判断')

@dataclass(frozen=True)
class Artifact:
    id: str
    text: str
    file: str
    start_line: int
    end_line: int
    diagram_type: str = ''
    responsibility: str = ''

    def source(self):
        return {'file': self.file, 'start_line': self.start_line, 'end_line': self.end_line}

@dataclass
class CheckRequest:
    requirements: list[Artifact]
    models: list[Artifact]
    rules: list[dict] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    run_id: str = ''

class Retriever(Protocol):
    def retrieve(self, request: CheckRequest) -> list[dict]: ...

class ModelClient(Protocol):
    def complete(self, system: str, payload: dict) -> dict: ...

class RelationClassifier(Protocol):
    def classify(self, pairs: list[dict], runner) -> list[dict]: ...
