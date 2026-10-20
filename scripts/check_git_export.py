"""Read-only export check; never prints matched secret values. No network calls."""
from pathlib import Path
import subprocess, re, json, hashlib, collections
ROOT = Path(__file__).resolve().parents[1]
def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])
PATTERNS = {
 'private_key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'provider_token': rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{35}|AKIA[A-Z0-9]{16})',
 'credential_url': rb'https?://[^\s/:]+:[^\s/@]+@',
 'jwt': rb'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}',
 'literal_secret': rb'''(?im)(?<![A-Za-z0-9_])["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)["']?\s*[:=]\s*["']([^"'\r\n]{8,})["']''',
}
def scan(data, label):
    hits=[]
    for name,pat in PATTERNS.items():
        for m in re.finditer(pat,data):
            value = m.group(1) if name=='literal_secret' else m.group()
            if name=='literal_secret' and (any(x in value.lower() for x in (b'example',b'test',b'dummy',b'redact',b'placeholder',b'fake',b'your_',b'********')) or not re.fullmatch(rb'[A-Za-z0-9_+/=.@!$%-]{8,}',value)):
                continue
            file_path=label.split(':',2)[-1] if label.startswith('git:') else label
            fixtures={b'do-not-inherit',b'never-save',b'also-private',b'private-value',b'never-store-this',b'never-in-child',b'SENTINEL_SECRET',b'do-not-store'}
            if file_path.startswith('tests/') and (value in fixtures or (name=='credential_url' and value==b'https://' + b'user:password@')):
                continue  # Reviewed synthetic redaction/path-validation fixtures.
            hits.append({'path':label,'rule':name,'line':data[:m.start()].count(b'\n')+1})
    return hits

def main():
    names=[x for x in git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0') if x]
    names=sorted(set(names)-{'reports/development/2026-10-20_GITHUB/export_check.json'}); manifest=[]; findings=[]
    for name in names:
        p=ROOT/name
        if not p.is_file(): continue
        data=p.read_bytes()
        manifest.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        findings.extend(scan(data,name))
        if len(data)>25*1024*1024: findings.append({'path':name,'rule':'large_file'})
    objects=git('rev-list','--all','--objects').decode().splitlines()
    labels={row.partition(' ')[0]:row.partition(' ')[2] for row in objects}
    proc=subprocess.Popen(['git','-C',str(ROOT),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    count=0; history=[]
    for oid,name in labels.items():
        proc.stdin.write((oid+'\n').encode()); proc.stdin.flush()
        header=proc.stdout.readline().split()
        if len(header)!=3: raise RuntimeError('Unexpected Git object header')
        size=int(header[2]); data=proc.stdout.read(size); proc.stdout.read(1)
        if header[1]!=b'blob': continue
        count+=1
        history.extend(scan(data,'git:'+oid+':'+name))
    proc.stdin.close(); proc.wait()
    result={'status':'passed' if not findings and not history else 'review_required', 'candidate_count':len(manifest),'candidate_bytes':sum(x['bytes'] for x in manifest),'history_blob_count':count,'findings':findings,'history_findings':history,'limitations':'Heuristic scan; no guarantee against unknown secret formats. Internal refs inspected but must not be pushed. Production data excluded, not backed up.', 'files':manifest}
    out=ROOT/'reports/development/2026-10-20_GITHUB'
    out.mkdir(parents=True,exist_ok=True)
    (out/'export_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 2
if __name__=='__main__': raise SystemExit(main())
