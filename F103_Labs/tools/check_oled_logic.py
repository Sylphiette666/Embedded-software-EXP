"""Offline SSD1306 regression using the real lab-8 AXF GPIOB instructions.

GPIO output edges are decoded as I2C; the model drives ACK on SDA only during
the ninth clock and implements SSD1306 command/page memory semantics. No OLED
function is stubbed. Only OLED_BusDelay's pure CPU delay is skipped. Time and
key/IRQ events are injected, so PASS is NOT a physical display acceptance test.
"""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
GPIOB=0x40010C00
EXPECTED_INIT=[0xAE,0xD5,0x80,0xA8,0x3F,0xD3,0x00,0x40,
               0x8D,0x14,0x20,0x02,0xA1,0xC8,0xDA,0x12,
               0x81,0x7F,0xD9,0xF1,0xDB,0x20,0xA4,0xA6,0x2E]


class SSD1306Bus:
    """Wired-AND open-drain bus with one optional SSD1306 slave."""
    ARG_COUNTS={0x20:1,0x21:2,0x22:2,0x81:1,0x8D:1,0xA8:1,
                0xD3:1,0xD5:1,0xD9:1,0xDA:1,0xDB:1}

    def __init__(self,address=0x3C,stuck_scl=False,stuck_sda=False):
        self.address=address
        self.stuck_scl=stuck_scl
        self.stuck_sda=stuck_sda
        self.scl=not stuck_scl
        self.sda=not stuck_sda
        self.drive_ack=False
        self.active=False
        self.bit_count=0
        self.byte=0
        self.ack_phase=None
        self.ack_value=False
        self.selected=False
        self.need_control=True
        self.continuation=False
        self.data_mode=False
        self.transactions=[]
        self.current=None
        self.command_bytes=[]
        self.command_events=[]
        self.data_writes=[]
        self.ram=bytearray(1024)
        self.page=0
        self.column=0
        self.addressing_mode=2
        self.column_start,self.column_end=0,127
        self.page_start,self.page_end=0,7
        self.pending_command=None
        self.pending_arguments=[]
        self.display_on=False
        self.scl_rising_edges=0
        self.ack_slots=0

    def update(self,scl_release,sda_release,ms=0):
        old_scl,old_sda=self.scl,self.sda
        scl=bool(scl_release and not self.stuck_scl)
        sda=bool(sda_release and not self.stuck_sda and not self.drive_ack)
        if old_scl and scl and old_sda!=sda:
            if not sda:
                self.start(ms)
            elif self.active:
                self.stop(ms)
        elif not old_scl and scl:
            self.scl_rising_edges+=1
            if self.active:
                if self.ack_phase=='clock':
                    self.ack_slots+=1
                    self.ack_phase='finish'
                elif self.ack_phase is None:
                    self.byte=(self.byte<<1)|int(sda)
                    self.bit_count+=1
                    if self.bit_count==8:
                        self.ack_value=self.receive_byte(self.byte,ms)
                        self.ack_phase='prepare'
        elif old_scl and not scl and self.active:
            if self.ack_phase=='prepare':
                self.drive_ack=self.ack_value
                self.ack_phase='clock'
            elif self.ack_phase=='finish':
                self.drive_ack=False
                self.ack_phase=None
                self.bit_count=0
                self.byte=0
        self.scl=scl
        self.sda=bool(sda_release and not self.stuck_sda and not self.drive_ack)

    def start(self,ms):
        if self.active and self.current is not None:
            self.current['repeated_start']=True
            self.transactions.append(self.current)
        self.active=True
        self.bit_count=0
        self.byte=0
        self.ack_phase=None
        self.drive_ack=False
        self.selected=False
        self.need_control=True
        self.current={'start_ms':ms,'bytes':[],'ack':[],'data_bytes':0,'commands':[]}

    def stop(self,ms):
        self.current['stop_ms']=ms
        self.transactions.append(self.current)
        self.current=None
        self.active=False
        self.drive_ack=False
        self.ack_phase=None

    def receive_byte(self,value,ms):
        is_address=not self.current['bytes']
        self.current['bytes'].append(value)
        if is_address:
            self.selected=self.address is not None and value==(self.address<<1)
            ack=self.selected
        else:
            ack=self.selected
            if ack:
                if self.need_control:
                    assert value&0x3F==0, f'Invalid SSD1306 control byte {value:#x}'
                    self.data_mode=bool(value&0x40)
                    self.continuation=bool(value&0x80)
                    self.need_control=False
                elif self.data_mode:
                    self.write_data(value,ms)
                    if self.continuation:self.need_control=True
                else:
                    self.current['commands'].append(value)
                    self.command(value,ms)
                    if self.continuation:self.need_control=True
        self.current['ack'].append(bool(ack))
        return bool(ack)

    def command(self,value,ms):
        self.command_bytes.append(value)
        if self.pending_command is not None:
            self.pending_arguments.append(value)
            if len(self.pending_arguments)==self.ARG_COUNTS[self.pending_command]:
                command=self.pending_command
                arguments=self.pending_arguments
                self.command_events.append((command,arguments[:],ms))
                if command==0x20:self.addressing_mode=arguments[0]
                elif command==0x21:
                    self.column_start,self.column_end=arguments
                    self.column=self.column_start
                elif command==0x22:
                    self.page_start,self.page_end=arguments
                    self.page=self.page_start
                self.pending_command=None
                self.pending_arguments=[]
            return
        if value in self.ARG_COUNTS:
            self.pending_command=value
            self.pending_arguments=[]
            return
        self.command_events.append((value,[],ms))
        if 0xB0<=value<=0xB7:self.page=value&7
        elif 0x00<=value<=0x0F:self.column=(self.column&0xF0)|value
        elif 0x10<=value<=0x1F:self.column=(self.column&15)|((value&15)<<4)
        elif value==0xAE:self.display_on=False
        elif value==0xAF:self.display_on=True

    def write_data(self,value,ms):
        assert 0<=self.page<8 and 0<=self.column<128,(self.page,self.column)
        address=self.page*128+self.column
        self.ram[address]=value
        self.data_writes.append((address,value,ms))
        self.current['data_bytes']+=1
        if self.addressing_mode==1:
            self.page+=1
            if self.page>self.page_end:
                self.page=self.page_start
                self.column=self.column_start if self.column>=self.column_end else self.column+1
        else:
            self.column+=1
            end=127 if self.addressing_mode==2 else self.column_end
            if self.column>end:
                self.column=0 if self.addressing_mode==2 else self.column_start
                if self.addressing_mode==0:
                    self.page=self.page_start if self.page>=self.page_end else self.page+1


def new_machine(address=0x3C,**faults):
    from check_firmware_logic import Machine
    bus=SSD1306Bus(address,**faults)
    machine=Machine(8,oled_bus=bus,iterations_per_ms=1)
    for name in ('OLED_Init','OLED_Task','OLED_BusDelay','g_oled_framebuffer',
                 'oled_font_ascii','oled_font_completed'):
        assert name in machine.symbols,'Rebuild the new lab-8 OLED AXF first: '+name
    return machine


def u8(machine,name):
    return bytes(machine.uc.mem_read(machine.symbols[name],1))[0]


def framebuffer(machine):
    return bytes(machine.uc.mem_read(machine.symbols['g_oled_framebuffer'],1024))


def font_bytes(machine,name,size):
    return bytes(machine.uc.mem_read(machine.symbols[name],size))


def expected_frame(machine,blink=False,completed=False):
    ascii_font=font_bytes(machine,'oled_font_ascii',95*16)
    chinese=font_bytes(machine,'oled_font_completed',8*32)
    frame=bytearray(1024)
    for row,text in [(0,'LAB8 OLED READY'),(1,'BLINK: ON' if blink else 'BLINK: OFF'),
                     (3,'K1:BLINK K4:MSG')]:
        for index,character in enumerate(text):
            glyph=ascii_font[(ord(character)-32)*16:(ord(character)-31)*16]
            x=index*8;page=row*2
            frame[page*128+x:page*128+x+8]=glyph[:8]
            frame[(page+1)*128+x:(page+1)*128+x+8]=glyph[8:]
    if completed:
        assert all(any(chinese[i*32:(i+1)*32]) for i in range(8)), 'Blank Chinese font glyph'
        for index in range(8):
            glyph=chinese[index*32:(index+1)*32]
            frame[512+index*16:512+(index+1)*16]=glyph[:16]
            frame[640+index*16:640+(index+1)*16]=glyph[16:]
    return bytes(frame)


def settle(machine,limit=160):
    """Run actual main-loop service, bounded well above two 64-chunk frames."""
    for _ in range(limit):machine.run_slice(100)
    assert machine.oled_bus.ram==framebuffer(machine),'SSD1306 GDDRAM does not match the firmware framebuffer'
    assert max((t['data_bytes'] for t in machine.oled_bus.transactions),default=0)<=16


def initialization_checks(address):
    m=new_machine(address)
    bus=m.oled_bus
    assert u8(m,'g_oled_present')==1 and u8(m,'g_oled_address')==address
    assert m.variable('g_oled_error_count')==0
    assert (m.r32(GPIOB)>>24)&0xFF==0x66,'PB6/PB7 must be 2 MHz open-drain outputs'
    assert m.r32(0x40021018)&8,'GPIOB clock disabled'
    assert bus.command_bytes==EXPECTED_INIT,bus.command_bytes
    probes=[t for t in bus.transactions if len(t['bytes'])==1]
    expected_probes=[0x78] if address==0x3C else [0x78,0x7A]
    assert [t['bytes'][0] for t in probes]==expected_probes
    assert probes[-1]['ack']==[True]
    if address==0x3D:assert probes[0]['ack']==[False]
    assert bus.addressing_mode==2 and not bus.display_on
    assert m.variable('g_oled_frame_count')==0 and len(bus.data_writes)==0
    assert framebuffer(m)==expected_frame(m)
    # Call the actual task with no emulated main-loop shortcut. Each call must
    # send exactly one contiguous 16-byte span and keep AF until all 1024 bytes.
    for index in range(64):
        before=len(bus.data_writes)
        m.call('OLED_Task')
        assert len(bus.data_writes)-before==16
        assert [x[0] for x in bus.data_writes[before:]]==list(range(index*16,(index+1)*16))
        assert bus.display_on==(index==63)
    assert bus.ram==expected_frame(m)
    assert m.variable('g_oled_frame_count')==1
    assert bus.command_bytes[-1]==0xAF
    assert m.pure_delay_calls>=20000
    return m


def integration_checks():
    m=initialization_checks(0x3C)
    phrase='嵌入式实验完成啦'
    before=m.variable('g_oled_frame_count')
    m.keys(k4=True);m.advance(30)
    assert m.text.count(phrase)==1
    assert framebuffer(m)==expected_frame(m,completed=True)
    m.advance(150);settle(m)
    assert m.oled_bus.ram==expected_frame(m,completed=True)
    rendered=m.variable('g_oled_frame_count')
    assert rendered>before
    m.advance(1100)
    assert m.text.count(phrase)==1 and m.variable('g_oled_frame_count')==rendered,'Holding K4 retriggered rendering'
    m.keys();m.advance(30)

    # Trigger an actual partial frame, then press K1 while work remains.
    m.call('OLED_ShowCompleted');m.call('OLED_Task')
    before=m.variable('g_oled_frame_count')
    m.keys(k1=True);m.advance(25)
    assert m.leds==1 and 'BLINK ON' in m.text
    assert m.variable('g_oled_frame_count')==before,'K1 press was not tested during an unfinished frame'
    assert framebuffer(m)==expected_frame(m,blink=True,completed=True)
    m.keys();m.advance(30)
    m.advance(1050)
    assert m.leds==0
    m.keys(k4=True);m.advance(30)
    assert m.text.count(phrase)==2
    m.advance(1050)
    assert m.leds==1 and m.text.count(phrase)==2
    m.keys();m.advance(30)
    settle(m)
    assert m.oled_bus.ram==expected_frame(m,blink=True,completed=True)

    # Keep OLED work pending as K1 stops blinking; output must go off promptly.
    m.call('OLED_ShowCompleted');m.call('OLED_Task')
    m.keys(k1=True);m.advance(25)
    assert m.leds==0 and 'BLINK OFF' in m.text
    m.keys();m.advance(30);m.advance(1100)
    assert m.leds==0
    settle(m)
    assert m.oled_bus.ram==expected_frame(m,blink=False,completed=True)

    # The tick wrap path must retain a fresh 1-second blink schedule.
    m.set_time(0xFFFFFF00);m.press(k1=True)
    assert m.leds==1
    m.advance(1050);assert m.leds==0
    m.advance(1000);assert m.leds==1
    settle(m)
    assert m.oled_bus.ram==expected_frame(m,blink=True,completed=True)
    # Check the consecutive periodic edges after the new schedule starts.
    edges=[t for t,state in m.edges if t<3000 and state in (0,1)]
    assert len(edges)>=2
    assert all(t['data_bytes']<=16 for t in m.oled_bus.transactions)
    assert m.variable('g_oled_error_count')==0


def dirty_during_frame():
    m=initialization_checks(0x3D)
    m.call('OLED_ShowCompleted')
    for _ in range(40):m.call('OLED_Task')
    # Row 1 has already been transmitted. A mid-frame update must queue a
    # complete follow-up frame; otherwise the controller keeps the old label.
    m.call('OLED_SetBlink',1)
    for _ in range(24):m.call('OLED_Task')
    assert m.oled_bus.ram!=expected_frame(m,blink=True,completed=True)
    for _ in range(64):m.call('OLED_Task')
    assert m.oled_bus.ram==expected_frame(m,blink=True,completed=True)
    assert m.variable('g_oled_frame_count')==3


def offline_checks(kind):
    kwargs={'address':None} if kind=='nack' else {'stuck_'+kind:True}
    m=new_machine(**kwargs)
    assert u8(m,'g_oled_present')==0
    assert m.variable('g_oled_error_count')==1
    assert m.variable('g_oled_frame_count')==0
    assert not m.oled_bus.data_writes
    transaction_count=len(m.oled_bus.transactions)
    if kind=='nack':
        assert [t['bytes'] for t in m.oled_bus.transactions]==[[0x78],[0x7A]]
        assert all(t['ack']==[False] for t in m.oled_bus.transactions)
    m.press(k1=True);assert m.leds==1
    m.advance(1050);assert m.leds==0
    m.press(k4=True)
    assert framebuffer(m)==expected_frame(m,blink=True,completed=True)
    m.press(k1=True);assert m.leds==0
    m.advance(1100);assert m.leds==0
    assert len(m.oled_bus.transactions)==transaction_count,'Offline display retried indefinitely'
    assert m.variable('g_oled_error_count')==1


def fault_during_transfer():
    for fault in ('scl','sda'):
        m=initialization_checks(0x3C)
        m.call('OLED_ShowCompleted');m.call('OLED_Task')
        frames=m.variable('g_oled_frame_count')
        setattr(m.oled_bus,'stuck_'+fault,True)
        m.call('OLED_Task')
        assert u8(m,'g_oled_present')==0
        assert m.variable('g_oled_error_count')==1
        assert m.variable('g_oled_frame_count')==frames
        m.press(k1=True);assert m.leds==1
        m.advance(1050);assert m.leds==0


def main():
    path=ROOT/'Build/oled_logic_check_results.json'
    record={'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'physical_hardware_test':False,
            'method':'Actual Keil AXF executes GPIOB software-I2C; wired-AND ACK and SSD1306 GDDRAM model. Only OLED_BusDelay pure CPU wait is skipped; time/key IRQ events are injected.',
            'limitations':['No physical OLED connected; controller type, pixel orientation, font appearance, pull-ups and bus waveform still need board verification.',
                           'Simulated milliseconds do not represent real bit-bang transfer duration.'],
            'tests':[],'passed':False}
    tests=[('address_3c_initialization_64_chunks_and_display_on',lambda:initialization_checks(0x3C)),
           ('address_3d_fallback_and_complete_frame',lambda:initialization_checks(0x3D)),
           ('real_key_logic_during_display_hold_blink_stop_and_wrap',integration_checks),
           ('mid_frame_update_queues_complete_followup_frame',dirty_during_frame),
           ('missing_display_address_nack_is_bounded',lambda:offline_checks('nack')),
           ('scl_held_low_is_bounded',lambda:offline_checks('scl')),
           ('sda_held_low_recovery_is_bounded',lambda:offline_checks('sda')),
           ('bus_fault_during_refresh_keeps_keys_and_blink_alive',fault_during_transfer)]
    try:
        for name,test in tests:
            start=time.perf_counter()
            test()
            record['tests'].append({'name':name,'passed':True,'seconds':round(time.perf_counter()-start,3)})
            print('PASS',name,flush=True)
        record['passed']=True
    except Exception as exc:
        record['tests'].append({'name':name,'passed':False,'error':str(exc),'traceback':traceback.format_exc()})
        raise
    finally:
        image=ROOT/'Build/Keil_08_Application/08_Application.axf'
        record['axf_sha256']=hashlib.sha256(image.read_bytes()).hexdigest()
        record['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted((ROOT/'Labs/08_Application').glob('*')) if p.suffix in ('.c','.h')}
        path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print('ALL OLED OFFLINE CHECKS PASSED (not a physical display test)')


if __name__=='__main__':main()
