#!/usr/bin/env bash
set -euo pipefail
context_path=${1:?context required}; output_dir=${2:?output required}; publish_dir=${3:?publish directory required}
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"; : "${GITOPS_FEATURE_SIMOS_PROJECT_ID:?GITOPS_FEATURE_SIMOS_PROJECT_ID is required}"; : "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"
python3 - "$context_path" "$output_dir" "$publish_dir" <<'PY'
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
ctx_path,root_path,pub_path=map(Path,sys.argv[1:]); root=root_path.resolve()
def reject(m): raise SystemExit(f'feature Registry preflight rejected: {m}')
def confined(p):
    p=(root_path/p).resolve() if not p.is_absolute() else p.resolve()
    try: p.relative_to(root)
    except ValueError: reject(f'path outside output: {p}')
    if not p.is_file(): reject(f'missing file: {p}')
    return p
def safe(v):
    s=str(v or '')
    if not s or s in ('.','..') or '/' in s or '\\' in s or '..' in s: reject(f'unsafe registry file: {s!r}')
    return s
def dig(p):
    b=p.read_bytes(); return len(b),hashlib.md5(b).hexdigest(),hashlib.sha256(b).hexdigest()
try: c=json.loads(ctx_path.read_text())
except Exception as e: reject(f'invalid context: {e}')
if c.get('schema')!=3 or 'config_source' in c: reject('context must be schema 3 without config_source')
bid=str(c.get('build_id') or '')
if not re.fullmatch(r'T\d{14}_[A-Za-z0-9.-]+',bid): reject('invalid Feature build id')
reg=c.get('registry') or {}; project=reg.get('project')
if not isinstance(project,str) or not project: reject('invalid Registry target')
plans=[]
for kind,pkg,mname in [('resident','simos-resident','package-registry-result.json'),('deb','simos-debs','deb-package-registry-result.json')]:
    d=root_path/kind; mp=d/mname
    try: m=json.loads(mp.read_text())
    except Exception as e: reject(f'invalid {kind} manifest: {e}')
    if m.get('status') not in ('success','skipped'): reject(f'{kind} manifest status is not success or skipped')
    if m.get('tag','') not in ('',None): reject(f'{kind} manifest has non-empty tag')
    entries=m.get('files') or []
    if kind=='resident' and not entries:
        entries=[{'file':str(p.relative_to(d))} for p in d.rglob('*') if p.is_file() and p.name!=mname]
    if not isinstance(entries,list): reject(f'{kind} manifest files invalid')
    listed=set()
    for e in entries:
        if not isinstance(e,dict): reject('manifest entry invalid')
        rel=e.get('local_path') or e.get('file') or e.get('path')
        if not isinstance(rel,str) or rel.startswith('/') or '..' in Path(rel).parts or '\\' in rel: reject('invalid artifact path')
        p=confined(d/rel); listed.add(p); size,md5,sha=dig(p)
        for key,val in [('size',size),('md5',md5),('sha256',sha)]:
            if key in e and str(e[key]).lower()!=str(val).lower(): reject(f'{kind} digest mismatch for {rel}')
        name=safe(e.get('registry_file') or p.name)
        plans.append({'package_name':pkg,'registry_file':name,'local_path':str(p.relative_to(root_path)),'kind':kind,'size':size,'md5':md5,'sha256':sha,'nextcloud_path':f'{kind}/{rel}'})
    for p in d.rglob('*'):
        if p.is_file() and p not in listed and p.name!=mname and (p.suffix in ('.deb','.ddeb','.zip') or p.name.endswith('.tar.gz')): reject(f'unlisted {kind} package file: {p.relative_to(root_path)}')
seen=set()
for i in plans:
    k=(i['package_name'],i['registry_file'])
    if k in seen: reject(f'duplicate Registry target: {k}')
    seen.add(k); i['registry_url']=f"{os.environ['CI_API_V4_URL'].rstrip('/')}/projects/{os.environ['GITOPS_FEATURE_SIMOS_PROJECT_ID']}/packages/generic/{i['package_name']}/{bid}/{i['registry_file']}"
for i in plans:
    confined(root_path/i['local_path'])
for i in plans:
    subprocess.run(['curl','--fail','--silent','--show-error','--location','--header',f"JOB-TOKEN: {os.environ['CI_JOB_TOKEN']}",'--upload-file',str(confined(root_path/i['local_path'])),i['registry_url']],check=True)
pub_path.mkdir(parents=True,exist_ok=True)
(pub_path/'registry-result.json').write_text(json.dumps({'build_id':bid,'project':project,'resident':{'package_name':'simos-resident','package_version':bid},'deb':{'package_name':'simos-debs','package_version':bid},'files':plans},ensure_ascii=False,indent=2)+'\n')
PY
