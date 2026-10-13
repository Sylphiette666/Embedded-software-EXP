"""Read-only project/link/vector checks. Run after tools/build_keil.py."""
from pathlib import Path
import hashlib
import json
import struct
import time
import xml.etree.ElementTree as ET
from elftools.elf.elffile import ELFFile
from intelhex import IntelHex

ROOT=Path(__file__).resolve().parents[1]
(ROOT/'Build/project_check_results.json').unlink(missing_ok=True)
PROJECT=ROOT/'MDK/F103_Labs.uvprojx'
OPTIONS=ROOT/'MDK/F103_Labs.uvoptx'
targets=ET.parse(PROJECT).getroot().findall('./Targets/Target')
debug={t.findtext('TargetName'):t for t in ET.parse(OPTIONS).getroot().findall('Target')}
results=[]
assert len(targets)==8
for target in targets:
    name=target.findtext('TargetName')
    base='TargetOption/TargetCommonOption/'
    assert target.findtext(base+'Device')=='STM32F103C8'
    assert target.findtext(base+'PackID')=='Keil.STM32F1xx_DFP.2.4.1'
    assert 'Cortex-M3' in target.findtext(base+'Cpu') and 'FPU' not in target.findtext(base+'Cpu')
    assert target.findtext('TargetOption/DllOption/TargetDlgDllArguments')=='-pCM3'
    assert target.findtext('TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories/IRAM/Size')=='0x5000'
    assert target.findtext('TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories/IROM/Size')=='0x10000'
    assert target.findtext('uAC6')=='1'
    assert target.findtext(base+'CreateHexFile')=='1'
    assert target.findtext(base+'DebugInformation')=='1'
    defines=target.findtext('TargetOption/TargetArmAds/Cads/VariousControls/Define')
    assert 'STM32F10X_MD' in defines and 'USE_STDPERIPH_DRIVER' in defines
    linker=target.findtext('TargetOption/TargetArmAds/LDads/ScatterFile')
    assert (PROJECT.parent/linker.replace('\\','/')).resolve()==ROOT/'MDK/flash.sct'
    included=[]
    oled_sources=[]
    for f in target.findall('./Groups/Group/Files/File'):
        path=(PROJECT.parent/f.findtext('FilePath').replace('\\','/')).resolve()
        assert path.is_file(),path
        if path.name=='oled.c' and f.findtext('FileOption/CommonProperty/IncludeInBuild')!='0':
            oled_sources.append(path)
        if path.name=='main.c' and f.findtext('FileOption/CommonProperty/IncludeInBuild')!='0':
            included.append(path)
    assert included==[ROOT/'Labs'/name/'main.c'],included
    assert oled_sources==([ROOT/'Labs/08_Application/oled.c'] if name=='08_Application' else []),oled_sources
    d=debug[name]
    assert d.findtext('TargetOption/OPTTT/RunSim')=='0'
    assert d.findtext('TargetOption/OPTTT/RunTarget')=='1'
    assert d.findtext('TargetOption/DebugOpt/nTsel')=='6'
    flash=d.find("./TargetOption/TargetDriverDllRegistry/SetRegEntry[Key='ST-LINKIII-KEIL_SWO']/Name").text
    assert 'STM32F10x_128' in flash and '-FS08000000' in flash and '-FL010000' in flash
    assert '-D00(1BA01477)' in flash
    artifact=ROOT/'Build'/('Keil_'+name)/(name+'.hex')
    programmed=IntelHex(str(artifact))
    assert programmed.minaddr()==0x08000000 and programmed.maxaddr()<0x08010000
    assert artifact.with_suffix('.bin').read_bytes()==programmed.tobinstr(start=0x08000000,end=programmed.maxaddr()),name+' stale BIN'
    with (ROOT/'Build'/('Keil_'+name)/(name+'.axf')).open('rb') as stream:
        elf=ELFFile(stream)
        symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
        if name=='08_Application':
            assert all(s in symbols for s in ('OLED_Init','OLED_Task','g_oled_framebuffer'))
        else:
            assert 'OLED_Init' not in symbols and 'g_oled_framebuffer' not in symbols
        vector=next(s for s in elf.iter_sections() if s['sh_addr']==0x08000000 and s['sh_flags']&2)
        data=vector.data()
        assert struct.unpack_from('<I',data,0)[0] in range(0x20000004,0x20005001)
        assert struct.unpack_from('<I',data,4)[0]==symbols['Reset_Handler']
        expected={15:'SysTick_Handler',16+37:'USART1_IRQHandler'}
        if name[:2] in ('05','08','09'):expected[16+40]='EXTI15_10_IRQHandler'
        for slot,handler in expected.items():
            assert struct.unpack_from('<I',data,slot*4)[0]==symbols[handler],(name,handler)
        flash_bytes=ram_bytes=0
        for section in elf.iter_sections():
            if not section['sh_flags']&2 or not section['sh_size']:continue
            start=section['sh_addr'];end=start+section['sh_size']
            if 0x08000000<=start<0x08010000:
                assert end<=0x08010000;flash_bytes+=section['sh_size']
            elif 0x20000000<=start<0x20005000:
                assert end<=0x20005000;ram_bytes+=section['sh_size']
            else:raise AssertionError((name,section.name,hex(start)))
    results.append({'target':name,'passed':True,'main':str(included[0].relative_to(ROOT)),
                    'flash_section_bytes':flash_bytes,'ram_section_bytes':ram_bytes,
                    'vector_handlers':list(expected.values()),'bin_matches_hex':True})
    print('PASS',name,'project/link/vector checks')

sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
         for folder in ('App','Labs','MDK','Vendor') for p in sorted((ROOT/folder).rglob('*'))
         if p.is_file() and p.suffix in ('.c','.h','.s','.sct','.uvprojx','.uvoptx')}
out={'checked_at':time.strftime('%Y-%m-%d %H:%M:%S'),'results':results,'inputs_sha256':sources}
(ROOT/'Build/project_check_results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
