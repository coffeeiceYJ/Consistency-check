import argparse
import json
import os
from pathlib import Path
import sys
from .inputs import load_request
from .model_client import JSONModelClient, HTTPEmbedder
from .pipeline import check
from .reporting import write_report
from .retrieval import LiSSARetriever, ROOT

def main(argv=None):
    parser=argparse.ArgumentParser(description='需求—PlantUML 一致性检查；使用真实 LiSSA 检索组件。')
    parser.add_argument('--input',required=True,type=Path,help='JSON manifest; paths relative to manifest')
    parser.add_argument('--output',type=Path,help='New report directory')
    parser.add_argument('--mode',choices=['rules','live'],default='rules',help='rules: explicit rules only; live: four-perspective AI checking')
    parser.add_argument('--java-home',default=os.environ.get('JAVA_HOME'))
    parser.add_argument('--lissa-jar',type=Path)
    parser.add_argument('--endpoint',default=os.environ.get('REQCHECK_ENDPOINT'),help='Full HTTPS chat/completions endpoint')
    parser.add_argument('--model',default=os.environ.get('REQCHECK_MODEL'))
    parser.add_argument('--key-env',default='DEEPSEEK_API_KEY')
    parser.add_argument('--embedding-endpoint',help='Optional full HTTPS embeddings endpoint; otherwise local TF-IDF')
    parser.add_argument('--embedding-model')
    parser.add_argument('--embedding-key-env',default='EMBEDDING_API_KEY')
    parser.add_argument('--max-calls',type=int)
    parser.add_argument('--quiet',action='store_true')
    args=parser.parse_args(argv)
    try:
        request=load_request(args.input)
        if args.max_calls is not None: request.options['max_calls']=args.max_calls
        client=None; embedder=None
        if args.mode=='live':
            if not args.endpoint or not args.model: raise ValueError('Live mode requires --endpoint and --model (or REQCHECK_ENDPOINT/REQCHECK_MODEL)')
            client=JSONModelClient(args.endpoint,args.model,key_env=args.key_env,
                                   max_tokens=request.options.get('max_output_tokens',6000),
                                   timeout=request.options.get('model_timeout_seconds',120),
                                   thinking_mode=request.options.get('model_thinking','auto'))
        elif args.embedding_endpoint:
            raise ValueError('Remote embeddings require --mode live; rules mode stays offline')
        if args.embedding_endpoint:
            if not args.embedding_model: raise ValueError('--embedding-model is required with --embedding-endpoint')
            embedder=HTTPEmbedder(args.embedding_endpoint,args.embedding_model,key_env=args.embedding_key_env)
        retriever=LiSSARetriever(args.java_home,args.lissa_jar,embedder)
        def progress(event):
            if not args.quiet: print(json.dumps(event,ensure_ascii=False),file=sys.stderr,flush=True)
        report=check(request,retriever,client,progress=progress)
        directory=args.output or ROOT/'outputs'/request.run_id
        path=write_report(report,directory)
        print(json.dumps({'run_status':report['run_status'],'report':str(path/'report.md'),
                          'issues':str(path/'issues.json'),'issue_table':str(path/'issues.csv'),
                          'conflicts':report['issue_summary']['conflicts'],
                          'suspected_omissions':report['issue_summary']['suspected_omissions'],
                          'pending_review':report['issue_summary']['pending_review'],
                          'execution_failures':len(report['execution_failures'])},ensure_ascii=False))
        if report['run_status'] in ('partial','cancelled'): return 3
        return 1 if report['issue_summary']['conflicts'] or any(a['consistency']=='存在冲突' for a in report['assessments']) else 0
    except (ValueError,OSError,RuntimeError) as exc:
        # Input/transport errors use controlled messages; never dump request/response bodies.
        print(f'检查未启动或未写完报告：{exc}',file=sys.stderr)
        return 2

if __name__=='__main__': sys.exit(main())
