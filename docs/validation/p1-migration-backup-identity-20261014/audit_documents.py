from pathlib import Path
import difflib
import hashlib
import json
import re
import subprocess
import unicodedata
from urllib.parse import unquote

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
baseline = '76ab7d24c68a590de44d12fd6f749f5f98fcc6f4'

def save(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def original(path):
    result = subprocess.run(['git', 'show', f'{baseline}:{path}'], cwd=root, capture_output=True)
    return result.stdout if result.returncode == 0 else None

before = json.loads((root / 'docs/validation/p1-completion-repair-20261013/documents-before.json').read_text(encoding='utf-8'))
paths = {root / item['path'] for item in before}
paths.update((root / 'docs/一期工程整改').rglob('*.md'))
paths.update((root / 'docs/接口对接/进行中').rglob('*.md'))
paths.update((root / 'docs').glob('一期工程检查-*.md'))
paths.add(root / 'docs/修改日志/2026-10-14-一期欠缺边界续修与检查.md')
paths.add(root / 'docs/修改日志/2026-10-14-未证明边界隔离续检.md')
paths.add(root / 'docs/修改日志/2026-10-14-全量中断复核与后台验证.md')
paths.add(root / 'docs/现状.md')
paths.add(root / 'docs/修改日志/2026-10-14-迁移备份身份与回滚收尾续修.md')
paths.add(root / 'docs/修改日志/2026-10-14-零字节永久引用回收守卫.md')
paths.add(root / 'docs/修改日志/2026-10-14-状态材料与永久引用安全续修.md')
audit = []
for path in sorted(paths):
    name = path.relative_to(root).as_posix()
    old = original(name)
    current = path.read_bytes() if path.exists() else None
    if current is None:
        disposition, reason = 'deleted', 'Requires explicit recorded reason; unexpected deletion.'
    elif old is None:
        disposition, reason = 'added', 'Record current repair reasons, actual validation scope and remaining boundaries.'
    elif old.replace(b'\r\n', b'\n') == current.replace(b'\r\n', b'\n'):
        disposition, reason = 'retained', 'Still valid reference or historical evidence; avoid duplicating current state.'
    else:
        disposition, reason = 'precisely_update_current_state', 'Replace obsolete current conclusions; preserve stable IDs, historical inputs/results, failed evidence and signatures.'
    audit.append(dict(path=name, disposition=disposition, reason=reason,
        baseline_sha256=hashlib.sha256(old).hexdigest() if old is not None else None,
        current_sha256=hashlib.sha256(current).hexdigest() if current is not None else None))
save('document-audit.json', audit)
save('document-changes.json', [item for item in audit if item['disposition'] != 'retained'])

issues = []
links = 0
def slug(value):
    value = re.sub(r'[`*_]', '', value).lower()
    return ''.join(c for c in value if c in '- ' or unicodedata.category(c)[0] in ('L', 'N', 'M')).replace(' ', '-')

paths.add(out / 'README.md')
paths.add(root / 'docs/validation/p1-migration-closure-20261014/README.md')
paths.add(root / 'docs/validation/p1-fix-recovery-20261014/README.md')
paths.add(root / 'docs/validation/p1-unproven-audit-20261014/README.md')
paths.add(root / 'docs/validation/p1-final-closure-20261014/README.md')
for path in sorted(paths):
    content = path.read_text(encoding='utf-8')
    name = path.relative_to(root).as_posix()
    for href in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', content):
        if re.match(r'^[a-z]+:', href):
            continue
        local, sep, anchor = unquote(href).partition('#')
        target = (path.parent / local).resolve() if local else path
        links += 1
        if not target.exists():
            issues.append(dict(path=name, href=href, reason='missing target'))
        elif sep and target.suffix == '.md':
            headings = [slug(x) for x in re.findall(r'^#{1,6}\s+(.+)$', target.read_text(encoding='utf-8'), re.M)]
            if anchor not in headings:
                issues.append(dict(path=name, href=href, reason='missing heading'))
    width = None
    in_code = False
    for number, line in enumerate(content.splitlines(), 1):
        if line.startswith('```'):
            in_code = not in_code
        if in_code:
            continue
        if line.startswith('|'):
            count = len(re.split(r'(?<!\\)\|', line)) - 2
            if width is not None and width != count:
                issues.append(dict(path=name, line=number, reason='table width mismatch'))
            width = count
        else:
            width = None

ledger = (root / 'docs/接口对接/README.md').read_text(encoding='utf-8')
state = (root / 'docs/现状.md').read_text(encoding='utf-8')
contracts = []
for path in sorted((root / 'docs/接口对接/进行中').glob('*/contract.md')):
    content = path.read_text(encoding='utf-8')
    fields = {key: re.search(r'^' + key + r': (.+)$', content, re.M).group(1).strip('"')
              for key in ('contract_id','contract_version','contract_status','provider_implementation','consumer_implementation','verification_status','last_verified_commit')}
    identity, version = fields['contract_id'], fields['contract_version']
    assert re.search(r'^\| \[' + re.escape(identity) + r'\].*?reviewing，' + re.escape(version) + r' \|', ledger, re.M), (identity, 'ledger')
    assert '| ' + identity + ' | ' + version + ' |' in state, (identity, 'state')
    assert fields['contract_status'] == 'reviewing' and fields['verification_status'] == 'not_run'
    contracts.append(fields)
save('document-links.json', dict(documents=len(paths), local_links=links, issues=issues, contracts=contracts))

acceptance = []
acceptance_before = json.loads((root / 'docs/validation/p1-completion-repair-20261013/acceptance-unchanged.json').read_text(encoding='utf-8'))
for phase in (1, 2, 3):
    name = f'tests/acceptance/p{phase}/status.json'
    current = (root / name).read_bytes()
    # Git stores LF while this Windows checkout has CRLF: check both the
    # original worktree-byte hash and the normalized committed content.
    assert hashlib.sha256(current).hexdigest() == acceptance_before[f'p{phase}']['sha256'], name
    assert current.replace(b'\r\n', b'\n') == original(name).replace(b'\r\n', b'\n'), name
    data = json.loads(current)
    assert all(item['result'] != 'verified' for item in data['results'])
    acceptance.append(dict(path=name, unchanged=True, sha256=hashlib.sha256(current).hexdigest()))
save('acceptance-unchanged.json', acceptance)
assert not issues, issues
assert not any(item['disposition'] == 'deleted' for item in audit), 'unreviewed deleted documentation'
print(json.dumps(dict(audited=len(audit), checked=len(paths), links=links, issues=len(issues), acceptance_unchanged=True), ensure_ascii=False))
