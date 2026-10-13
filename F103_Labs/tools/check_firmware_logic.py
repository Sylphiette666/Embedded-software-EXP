"""Run compiled Keil ARM code with modelled GPIO/UART and an injected ms clock.

Requires unicorn==2.1.4 and pyelftools. This is a software regression test,
not a board/peripheral acceptance test. No probe, COM port or flash is touched.
"""
from pathlib import Path
import hashlib
import json
import struct
import time
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[1]
GPIOA=0x40010800
GPIOB=0x40010C00
GPIOC=0x40011000
EXTI=0x40010400
USART=0x40013800
STOP=0x0800F000
MASK=0xFFFFFFFF
results=[]

class Machine:
    def __init__(self,number,oled_bus=None,iterations_per_ms=25):
        self.oled_bus=oled_bus
        if number==8 and self.oled_bus is None:
            from check_oled_logic import SSD1306Bus
            self.oled_bus=SSD1306Bus()
        self.iterations_per_ms=iterations_per_ms
        self.lab=next(p for p in (ROOT/'Labs').iterdir() if p.name.startswith(f'{number:02}_'))
        self.axf=ROOT/'Build'/('Keil_'+self.lab.name)/(self.lab.name+'.axf')
        self.uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        for base,size in [(0x08000000,0x10000),(0x20000000,0x5000),(0xE000E000,0x2000)]:
            self.uc.mem_map(base,size)
        self.peripherals=bytearray(0x30000)
        self.uc.mmio_map(0x40000000,0x30000,self.mmio_read,None,self.mmio_write,None)
        with self.axf.open('rb') as f:
            elf=ELFFile(f)
            self.symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
            for section in elf.iter_sections():
                if section['sh_flags'] & 2 and section['sh_type']!='SHT_NOBITS' and section['sh_size']:
                    self.uc.mem_write(section['sh_addr'],section.data())
        self.tx=bytearray()
        self.edges=[]
        self.ms=0
        self.block_budget=None
        boundary={1:'Time_Now',2:'Delay_Ms',3:'Delay_Ms',4:'Keys_Poll',
                  5:'Keys_PollIRQ',6:'UART_GetChar',7:'Time_Now',
                  8:'Keys_PollIRQ',9:'Keys_PollIRQ'}[number]
        self.boundary=self.symbols[boundary]&~1
        self.w32(GPIOA+8,(1<<15)|(1<<12))
        if self.oled_bus is not None:
            self.w32(GPIOB,0x44444444)
            self.w32(GPIOB+4,0x44444444)
            self.w32(GPIOB+8,0x00C0)
        self.w32(0x40021004,0x001D040A) # PLL HSE x9, SYSCLK=72M, APB1 /2
        self.w32(USART,0x80)  # TXE; no receive byte pending.
        self.uc.hook_add(UC_HOOK_CODE,self.on_block,begin=self.boundary,end=self.boundary+1)
        self.pure_delay_calls=0
        if self.oled_bus is not None and 'OLED_BusDelay' in self.symbols:
            delay=self.symbols['OLED_BusDelay']&~1
            self.uc.hook_add(UC_HOOK_CODE,self.skip_oled_cpu_delay,begin=delay,end=delay+1)
        self.uc.reg_write(UC_ARM_REG_SP,0x20004FF0)
        self.uc.reg_write(UC_ARM_REG_LR,STOP|1)
        self.pc=self.symbols['main']|1
        self.run_slice(1000)

    def r32(self,addr):
        if 0x40000000<=addr<0x40030000:
            offset=addr-0x40000000
            return int.from_bytes(self.peripherals[offset:offset+4],'little')
        return struct.unpack('<I',self.uc.mem_read(addr,4))[0]
    def w32(self,addr,value):
        data=struct.pack('<I',value&MASK)
        if 0x40000000<=addr<0x40030000:
            offset=addr-0x40000000
            self.peripherals[offset:offset+4]=data
        else:self.uc.mem_write(addr,data)
    def variable(self,name):return self.r32(self.symbols[name])
    def mmio_read(self,uc,offset,size,user):
        if self.oled_bus is not None and offset==GPIOB+8-0x40000000:
            value=self.r32(GPIOB+8)&~0x00C0
            value|=self.oled_bus.scl<<6
            value|=self.oled_bus.sda<<7
            return value&((1<<(size*8))-1)
        return int.from_bytes(self.peripherals[offset:offset+size],'little')
    def mmio_write(self,uc,offset,size,value,user):
        addr=0x40000000+offset
        self.exti_before=self.r32(EXTI+0x14)
        self.peripherals[offset:offset+size]=value.to_bytes(size,'little')
        if addr in (GPIOA+0x10,GPIOA+0x14):
            old=self.r32(GPIOA+0x0C)
            if addr==GPIOA+0x14:
                new=old & ~(value&0xFFFF)
            else:
                new=(old & ~((value>>16)&0xFFFF)) | (value&0xFFFF)
            self.w32(GPIOA+0x0C,new)
            self.w32(GPIOA+8,(self.r32(GPIOA+8)&~3)|(new&3))
            if (old^new)&3:self.edges.append((self.ms,(~new)&3))
        elif addr==USART+4:
            self.tx.append(value&0xFF)
        elif addr==EXTI+0x14:
            self.w32(addr,self.exti_before & ~value)
        if self.oled_bus is not None and GPIOB<=addr<=GPIOB+0x14:
            if addr in (GPIOB+0x10,GPIOB+0x14):
                old=self.r32(GPIOB+0x0C)
                new=(old&~(value&0xFFFF)) if addr==GPIOB+0x14 else ((old&~((value>>16)&0xFFFF))|(value&0xFFFF))
                self.w32(GPIOB+0x0C,new)
            crl=self.r32(GPIOB);odr=self.r32(GPIOB+0x0C)
            released=[]
            for pin in (6,7):
                mode=(crl>>(4*pin))&15
                released.append(bool(odr&(1<<pin)) if mode&3 else True)
            self.oled_bus.update(*released,ms=self.ms)

    def skip_oled_cpu_delay(self,uc,addr,size,user):
        # Only skip the documented pure CPU delay. All GPIO/I2C routines,
        # ACK sampling, initialization commands, drawing and transfers execute.
        self.pure_delay_calls+=1
        uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))

    def on_block(self,uc,addr,size,user):
        if self.block_budget is not None:
            if self.block_budget==0:
                uc.emu_stop()
            else:
                self.block_budget-=1

    def run_slice(self,count=None):
        # Resume only at a function entry, never in a Thumb IT block or a partial
        # emulated basic block. This also keeps input injection deterministic.
        if count is None:count=self.iterations_per_ms*100
        self.block_budget=max(1,count//100)
        if self.pc != self.boundary:self.block_budget=0
        self.uc.emu_start(self.pc|1,STOP,count=1000000)
        self.block_budget=None
        self.pc=self.uc.reg_read(UC_ARM_REG_PC)
        assert self.pc==self.boundary, 'Did not reach the main-loop boundary'

    def call(self,name,arg=0):
        context=self.uc.context_save()
        self.uc.reg_write(UC_ARM_REG_SP,0x20004000)
        self.uc.reg_write(UC_ARM_REG_LR,STOP|1)
        self.uc.reg_write(UC_ARM_REG_R0,arg)
        self.uc.emu_start(self.symbols[name]|1,STOP,count=100000)
        assert self.uc.reg_read(UC_ARM_REG_PC)==STOP, 'Function did not return: '+name
        value=self.uc.reg_read(UC_ARM_REG_R0)
        self.uc.context_restore(context)
        return value

    def advance(self,milliseconds):
        for _ in range(milliseconds):
            self.ms=(self.ms+1)&MASK
            self.w32(self.symbols['g_millis'],self.ms)
            self.run_slice()

    def set_time(self,ms):
        self.ms=ms&MASK
        self.w32(self.symbols['g_millis'],self.ms)

    def keys(self,k1=False,k4=False,interrupts=True):
        for addr,bit,down,line,handler in [
            (GPIOA+8,15,k1,15,'EXTI15_10_IRQHandler'),
            (GPIOA+8,12,k4,12,'EXTI15_10_IRQHandler')]:
            old=self.r32(addr)
            self.w32(addr,old&~(1<<bit) if down else old|(1<<bit))
            if down and (old&(1<<bit)) and interrupts and (self.r32(EXTI)&(1<<line)):
                self.w32(EXTI+0x14,self.r32(EXTI+0x14)|(1<<line))
                self.call(handler)
                self.w32(EXTI+0x14,0)
        self.run_slice()

    def press(self,k1=False,k4=False):
        self.keys(k1,k4);self.advance(30)
        self.keys();self.advance(30)

    def receive(self,data,run=True):
        for value in data:
            self.w32(USART+4,value);self.w32(USART,0xA0)
            self.call('USART1_IRQHandler')
            self.w32(USART,0x80)
            if run:self.run_slice()

    @property
    def leds(self):return (~self.r32(GPIOA+0x0C))&3
    @property
    def text(self):return self.tx.decode('utf-8')

def check(name,function):
    start=time.perf_counter()
    function()
    results.append({'test':name,'passed':True,'seconds':round(time.perf_counter()-start,3)})
    print('PASS',name,flush=True)

def initialization():
    # The shared EXTI/UART/SysTick platform is also initialized by lab 5.
    # Lab 8's display receives a separate GPIO-edge/SSD1306 model below.
    m=Machine(5)
    assert m.variable('SystemCoreClock')==72000000
    assert m.r32(0xE000E014)==71999
    assert m.r32(0xE000E010)&7==7
    assert m.r32(GPIOA)&0xFF==0x22 # LEDs: 2 MHz push-pull
    assert (m.r32(GPIOA+4)>>4)&0xFF==0x4B # PA9 AF PP, PA10 floating input
    assert (m.r32(GPIOA+4)>>16)&0xF==8 # PA12 pull-up
    assert (m.r32(GPIOA+4)>>28)&0xF==8 # PA15 pull-up
    assert (m.r32(0x40010004)>>24)&7==2 # JTAG disabled, SWD retained
    assert m.r32(EXTI)&0x9000==0x9000
    assert m.r32(EXTI+0x0C)&0x9000==0x9000
    assert m.r32(0x40010014)&0xFFFF==0 # EXTI12/15 mapped to A
    assert m.r32(USART+8)==625 # 72 MHz / 115200
    assert (m.r32(USART+0x0C)&((1<<13)|(1<<5)|12))==((1<<13)|(1<<5)|12)
    assert 'LAB 5' in m.text and m.leds==0
    m.set_time(0xFFFFFFFF);m.call('SysTick_Handler')
    assert m.variable('g_millis')==0

def polling():
    m=Machine(4)
    m.keys(True);m.advance(5);m.keys();m.advance(5)
    m.keys(True);m.advance(19);assert m.leds==0
    m.advance(1);assert m.leds==3
    state_lines=m.text.count('STATE ')
    m.advance(500);assert m.leds==3 and m.text.count('STATE ')==state_lines
    m.keys();m.advance(20);m.press(True);assert m.leds==0
    m.press(k4=True);assert m.leds==1
    m.press(k4=True);assert m.leds==2
    m.press(True);assert m.leds==1
    m=Machine(4);m.press(True,True);assert m.leds==1

def rollover():
    for number in (4,5):
        m=Machine(number);m.set_time(0xFFFFFFF5)
        m.keys(k4=True);m.advance(19);assert m.leds==0
        m.advance(1);assert m.leds==(1 if number==4 else 2)
        m.keys();m.advance(30);m.press(k4=True)
        assert m.leds==(2 if number==4 else 0)

def interrupts():
    m=Machine(5)
    m.press(True);assert m.leds==1
    m.press(k4=True);assert m.leds==3
    m.press(True);assert m.leds==2
    m.press(k4=True);assert m.leds==0
    m.keys(True,True);m.advance(30);assert m.leds==3
    m.advance(500);assert m.leds==3
    m=Machine(5)
    # A sampled low level without an injected IRQ must not trigger the IRQ application.
    m.keys(k4=True,interrupts=False);m.advance(30);assert m.leds==0
    m.keys();m.advance(30);m.press(k4=True);assert m.leds==2
    m=Machine(5)
    for _ in range(4):
        m.keys(k4=True);m.advance(2);m.keys();m.advance(2)
    assert m.leds==0
    m.keys(k4=True);m.advance(20);assert m.leds==2
    assert m.variable('g_key1_irq_count')==5
    assert m.text.count('STATE ')==1

def serial():
    m=Machine(6)
    for char,expected in [(b'Y',1),(b'N',0),(b'X',0),(b'Y',1),(b'N',0),(b'y',0),(b'\r\n',0)]:
        m.receive(char);assert m.leds==expected
    m.receive(b'?');assert 'STATE ' in m.text and m.variable('g_uart_overruns')==0
    # Exercise actual RX ring wrap and recovery after full buffer.
    for _ in range(140):m.receive(b'YN')
    assert m.leds==0 and m.variable('g_uart_overruns')==0
    m.receive(b'X'*128,run=False)
    assert m.variable('g_uart_overruns')==1
    m.run_slice(50000);m.receive(b'Y');assert m.leds==1

def integrated():
    from check_oled_logic import integration_checks
    integration_checks()

def periodic():
    for number,interval in [(1,500),(7,1000)]:
        m=Machine(number);m.advance(interval*3+100)
        edges=[e for e in m.edges if e[0]>0]
        assert len(edges)>=3,(number,edges)
        for a,b in zip(edges,edges[1:]):
            assert abs(b[0]-a[0]-interval)<=1,(number,edges)
    # Real-board wall-clock blinking has NOT yet been tested on F103.
    assert Machine(2).leds==1
    assert Machine(3).leds==1

def shared_vector():
    m=Machine(5)
    m.w32(GPIOA+8,m.r32(GPIOA+8)&~0x9000)
    m.w32(EXTI+0x14,0x9000)
    m.call('EXTI15_10_IRQHandler')
    assert m.variable('g_key0_irq_count')==1
    assert m.variable('g_key1_irq_count')==1
    assert m.r32(EXTI+0x14)==0
    m.advance(30)
    assert m.leds==3

if __name__=='__main__':
    (ROOT/'Build/logic_check_results.json').unlink(missing_ok=True)
    for name,fn in [('register_initialization_and_systick_wrap',initialization),
                    ('polling_bounce_hold_mutual_exclusion_and_simultaneous_keys',polling),
                    ('debounce_uint32_rollover',rollover),('two_exti_inputs_and_bounce',interrupts),
                    ('uart_commands_ring_wrap_and_overflow_recovery',serial),
                    ('lab8_oled_while_blinking_stop_and_rollover',integrated),
                    ('periodic_outputs_labs_1_7_and_initial_outputs_2_3',periodic),
                    ('shared_exti_vector_simultaneous_pending_bits',shared_vector)]:
        check(name,fn)
    images={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'Build').glob('Keil_*/*.axf'))}
    out={'method':'Unicorn executes actual Keil AXF; GPIO/UART and OLED I2C/GDDRAM modelled; time and IRQ entry injected; only OLED_BusDelay pure CPU delay skipped.',
         'physical_hardware_test':False,'checked_at':time.strftime('%Y-%m-%d %H:%M:%S'),
         'tests':results,'images_sha256':images}
    (ROOT/'Build/logic_check_results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('ALL SOFTWARE CHECKS PASSED')
