#!/usr/bin/env bash
set -euo pipefail
kind=${1:?kind required}; context_path=${2:?context required}; output_dir=${3:?output required}; publish_dir=${4:?publish directory required}
case "$kind" in resident|deb) ;; *) echo "feature Registry: kind must be resident or deb" >&2; exit 2;; esac
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"; : "${CI_PROJECT_ID:?CI_PROJECT_ID is required}"; : "${CI_PROJECT_PATH:?CI_PROJECT_PATH is required}"; : "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"
command -v python3 >/dev/null 2>&1 || { echo "feature Registry: python3 is required in the build image" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "feature Registry: curl is required in the build image" >&2; exit 1; }
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
if project != os.environ['CI_PROJECT_PATH']: reject('Registry target must be the Workbench project')
pkg,mname=(('feature-resident','package-registry-result.json') if kind=='resident' else ('feature-debs','deb-package-registry-result.json'))
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

# The formal SimOS entrypoints always emit metadata beside the package files
# (for example deb-package-info/build-info.json and the copied registry
# manifest).  Those files are part of the trusted build output and must be
# published as well; treating them as unexpected files makes every successful
# deb build fail before the first upload.  Add them deterministically using a
# path-derived Registry leaf so nested build-info.json files cannot collide.
for p in sorted(d.rglob('*')):
    if not p.is_file() or p.is_symlink() or p == mp or p in listed:
        continue
    relp = p.relative_to(d)
    is_metadata = (
        'metadata' in relp.parts
        or p.suffix in {'.json', '.txt', '.md5', '.sha256', '.xml', '.env'}
        or 'build-info' in p.name
        or 'checksum' in p.name
        or p.name in {'vehicle.info', 'version.info', 'software.yaml'}
    )
    if not is_metadata:
        continue
    listed.add(p)
    size, md5, sha = dig(p)
    name = safe(str(relp).replace('/', '-'))
    plans.append({
        'package_name': pkg,
        'registry_file': name,
        'local_path': str(p.relative_to(root)),
        'kind': kind,
        'size': size,
        'md5': md5,
        'sha256': sha,
        'nextcloud_path': f'{kind}/{relp.as_posix()}',
    })
for p in d.rglob('*'):
    if p.is_file() and p not in listed and p != mp:
        relp = p.relative_to(d)
        is_metadata = 'metadata' in relp.parts or p.suffix in {'.json', '.txt', '.md5', '.sha256', '.xml', '.env'} or 'build-info' in p.name or 'checksum' in p.name
        is_package = p.suffix in ('.deb', '.ddeb', '.zip') or p.name.endswith('.tar.gz')
        if is_package or is_metadata:
            reject(f'unlisted {kind} package file: {p.relative_to(root)}')
seen=set()
for i in plans:
    k=(i['package_name'],i['registry_file'])
    if k in seen: reject(f'duplicate Registry target: {k}')
    seen.add(k); i['registry_url']=f"{os.environ['CI_API_V4_URL'].rstrip('/')}/projects/{os.environ['CI_PROJECT_ID']}/packages/generic/{i['package_name']}/{bid}/{i['registry_file']}"
for i in plans:
    confined(root/i['local_path'])
for i in plans:
    local = confined(root / i['local_path'])
    print(f"feature Registry: uploading {i['kind']} {i['local_path']} -> {i['registry_file']}", flush=True)
    try:
        subprocess.run([
            'curl', '--fail', '--silent', '--show-error', '--location',
            '--retry', '3', '--retry-delay', '2',
            '--header', f"JOB-TOKEN: {os.environ['CI_JOB_TOKEN']}",
            '--upload-file', str(local), i['registry_url'],
        ], check=True)
    except subprocess.CalledProcessError as exc:
        reject(f"Registry upload failed for {i['local_path']} (curl exit {exc.returncode})")
pub_path.mkdir(parents=True,exist_ok=True)
(pub_path/'registry-result.json').write_text(json.dumps({'build_id':bid,'project':project,'kind':kind,kind:{'package_name':pkg,'package_version':bid},'files':plans},ensure_ascii=False,indent=2)+'\n')
PY
