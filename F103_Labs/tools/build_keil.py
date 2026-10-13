"""Rebuild and validate each target using uVision, with its window hidden."""
from pathlib import Path
import subprocess
import json
import argparse
import datetime
import hashlib
import re
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--keil',default=r'D:\Keil5\UV4\UV4.exe')
args=parser.parse_args()
if not Path(args.keil).is_file():
    raise SystemExit('Keil UV4.exe not found; set --keil to its location.')
(ROOT/'Build').mkdir(exist_ok=True)
startup=subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow=0
results=[]
(ROOT/'Build/keil_results.json').write_text('[]',encoding='utf-8')
for lab in sorted((ROOT/'Labs').iterdir()):
    if not (lab/'main.c').is_file():continue
    log=ROOT/'Build'/('keil_'+lab.name+'.log')
    log.unlink(missing_ok=True)  # An old PASS log must never validate a failed launch.
    started=datetime.datetime.now().astimezone().isoformat()
    p=subprocess.run([args.keil,'-r',str(ROOT/'MDK/F103_Labs.uvprojx'),
        '-t',lab.name,'-o',str(log),'-j0'],startupinfo=startup,cwd=ROOT/'MDK')
    text=log.read_bytes().decode('utf-8',errors='replace') if log.exists() else ''
    axf=ROOT/'Build'/('Keil_'+lab.name)/(lab.name+'.axf')
    hexfile=axf.with_suffix('.hex')
    binfile=axf.with_suffix('.bin')
    binfile.unlink(missing_ok=True)  # Never leave an old binary beside a new HEX.
    ok=(p.returncode==0 and re.search(r'\b0 Error\(s\), 0 Warning\(s\)\.',text) is not None
        and axf.is_file() and hexfile.is_file())
    if ok:
        fromelf=Path(args.keil).resolve().parents[1]/'ARM/ARMCLANG/bin/fromelf.exe'
        conversion=subprocess.run([str(fromelf),'--bin','--output',str(binfile),str(axf)],
                                  startupinfo=startup,cwd=ROOT/'MDK')
        ok=conversion.returncode==0 and binfile.is_file() and binfile.stat().st_size>0
    print(lab.name, 'PASS' if ok else 'FAIL')
    results.append({'target':lab.name,'exit_code':p.returncode,'passed':ok,
        'build_mode':'rebuild','started_at':started,
        'axf_sha256':hashlib.sha256(axf.read_bytes()).hexdigest() if ok else None,
        'bin_sha256':hashlib.sha256(binfile.read_bytes()).hexdigest() if ok else None,
        'hex_sha256':hashlib.sha256(hexfile.read_bytes()).hexdigest() if ok else None})
    (ROOT/'Build/keil_results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    if not ok:print(text);raise SystemExit(1)
