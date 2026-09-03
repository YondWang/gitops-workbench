#!/usr/bin/env bash
set -euo pipefail
kind=${1:?kind required}; context_path=${2:?context required}; output_dir=${3:?output required}; publish_dir=${4:?publish directory required}
case "$kind" in resident|deb) ;; *) echo "feature Registry: kind must be resident or deb" >&2; exit 2;; esac
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"; : "${GITOPS_FEATURE_SIMOS_PROJECT_ID:?GITOPS_FEATURE_SIMOS_PROJECT_ID is required}"; : "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"
python3 - "$kind" "$context_path" "$output_dir" "$publish_dir" <<'PY'
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
kind,ctx_path,root_path,pub_path=sys.argv[1],Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]); root=root_path.resolve(); pub_path=pub_path.resolve()
def reject(m): raise SystemExit(f'feature Registry preflight rejected: {m}')
def confined(p):
    p=(root/p).resolve() if not p.is_absolute() else p.resolve()
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
def locate(d, rel, expected):
    """Resolve formal manifest paths against the copied output tree.

    SimOS deb manifests historically store ``file`` as a basename while the
    file itself lives below deb-packages/ or deb_packages/.  Prefer an exact
    relative path, then search by basename and use recorded digests to avoid
    selecting an unrelated file.
    """
    if not isinstance(rel,str) or rel.startswith('/') or '..' in Path(rel).parts or '\\' in rel:
        reject('invalid artifact path')
    direct=(d/rel).resolve()
    candidates=[direct] if direct.is_file() else sorted(p for p in d.rglob(Path(rel).name) if p.is_file() and not p.is_symlink())
    if not candidates: reject(f'missing file: {d/rel}')
    if len(candidates)>1 and isinstance(expected,dict):
        matched=[]
        for candidate in candidates:
            size,md5,sha=dig(candidate)
            if ('size' not in expected or int(expected['size'])==size) and ('md5' not in expected or str(expected['md5']).lower()==md5) and ('sha256' not in expected or str(expected['sha256']).lower()==sha):
                matched.append(candidate)
        candidates=matched
    if len(candidates)!=1: reject(f'ambiguous artifact path: {rel}')
    return candidates[0]
try: c=json.loads(ctx_path.read_text())
except Exception as e: reject(f'invalid context: {e}')
if c.get('schema')!=3 or 'config_source' in c: reject('context must be schema 3 without config_source')
bid=str(c.get('build_id') or '')
if not re.fullmatch(r'T\d{14}_[A-Za-z0-9.-]+',bid): reject('invalid Feature build id')
reg=c.get('registry') or {}; project=reg.get('project')
if not isinstance(project,str) or not project: reject('invalid Registry target')
pkg,mname=(('simos-resident','package-registry-result.json') if kind=='resident' else ('simos-debs','deb-package-registry-result.json'))
plans=[]
d=root/kind; mp=d/mname
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
    p=locate(d, rel, e); p=confined(p); listed.add(p); size,md5,sha=dig(p)
    for key,val in [('size',size),('md5',md5),('sha256',sha)]:
        if key in e and str(e[key]).lower()!=str(val).lower(): reject(f'{kind} digest mismatch for {rel}')
    # Skipped resident manifests may omit ``files`` and are reconstructed from
    # the copied output tree. Preserve explicit formal names, but derive a
    # unique Registry leaf for discovered nested metadata files; basename()
    # would collide for e.g. build-info.json at two directory levels.
    fallback_name = rel.replace('/', '-')
    name=safe(e.get('registry_file') or fallback_name)
    plans.append({'package_name':pkg,'registry_file':name,'local_path':str(p.relative_to(root)),'kind':kind,'size':size,'md5':md5,'sha256':sha,'nextcloud_path':f'{kind}/{rel}'})
for p in d.rglob('*'):
    if p.is_file() and p not in listed and p.name!=mname:
        relp = p.relative_to(d)
        is_metadata = 'metadata' in relp.parts or p.suffix in {'.json', '.txt', '.md5', '.sha256', '.xml', '.env'} or 'build-info' in p.name or 'checksum' in p.name
        is_package = p.suffix in ('.deb', '.ddeb', '.zip') or p.name.endswith('.tar.gz')
        if is_package or is_metadata:
            reject(f'unlisted {kind} package file: {p.relative_to(root)}')
seen=set()
for i in plans:
    k=(i['package_name'],i['registry_file'])
    if k in seen: reject(f'duplicate Registry target: {k}')
    seen.add(k); i['registry_url']=f"{os.environ['CI_API_V4_URL'].rstrip('/')}/projects/{os.environ['GITOPS_FEATURE_SIMOS_PROJECT_ID']}/packages/generic/{i['package_name']}/{bid}/{i['registry_file']}"
for i in plans:
    confined(root/i['local_path'])
for i in plans:
    subprocess.run(['curl','--fail','--silent','--show-error','--location','--header',f"JOB-TOKEN: {os.environ['CI_JOB_TOKEN']}",'--upload-file',str(confined(root/i['local_path'])),i['registry_url']],check=True)
pub_path.mkdir(parents=True,exist_ok=True)
(pub_path/'registry-result.json').write_text(json.dumps({'build_id':bid,'project':project,'kind':kind,kind:{'package_name':pkg,'package_version':bid},'files':plans},ensure_ascii=False,indent=2)+'\n')
PY
