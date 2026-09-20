"""Package reproducible evidence; excludes dependencies, credentials and deployments."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/quant/research-20260920'
files=[ROOT/'quant/backtest.py',ROOT/'quant/test_backtest.py',ROOT/'runner/policy.py',
       ROOT/'docs/QUANT_RESEARCH_20260920.md',ROOT/'output/pdf/quant-research-20260920.pdf']
files += [p for p in (ROOT/'quant/research_20260920').iterdir() if p.suffix in ('.py','.md','.txt')]
files += list((ROOT/'data/quant/candles').glob('*.json'))
files += [ROOT/f'data/quant/v2-research-20260917-{kind}/results.json' for kind in ('history','recent')]
files += [p for p in OUT.iterdir() if p.suffix in ('.json','.md','.png') and p.name!='reproducibility-manifest.json']
files += list((OUT/'candles').glob('*.json'))
files += [ROOT/'data/reviews/status-2026-09-20'/name for name in ['v2-report.json','universe-report.json','audit.json','report.md']]
files=sorted(set(files))
manifest={'files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(OUT/'reproducibility-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
files.append(OUT/'reproducibility-manifest.json')
target=ROOT/'output/quant-research-20260920-reproducible.zip'
with ZipFile(target,'w',compression=ZIP_DEFLATED,compresslevel=6) as archive:
    for p in files: archive.write(p,p.relative_to(ROOT).as_posix())
with ZipFile(target) as archive:
    assert archive.testzip() is None
    for name,digest in manifest['files'].items():
        assert hashlib.sha256(archive.read(name)).hexdigest()==digest
print(json.dumps({'zip':str(target),'files':len(files),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}))
