"""Create the integrated F103/OLED lab report from local source and evidence.
Run with bundled Python after the document authoring marker. No hardware I/O.
"""
from pathlib import Path
import hashlib
import json
import textwrap
from docx.shared import Cm, Pt
from docx.oxml.ns import qn
from build_oled_guide import Guide
from build_board_validation_guide import LABS

ROOT=Path('D:/MDK5/F103_Labs')
WORK=Path('D:/Work/嵌入式实验')
OUTPUT=WORK/'嵌入式系统实验报告_STM32F103_OLED整合版_20261013.docx'

PRINCIPLES=[
 '复位后，处理器从向量表取得初始栈顶和 Reset_Handler，启动文件调用 SystemInit，再进入 C 运行环境和 main。芯片型号、启动文件、编译宏、链接地址与下载算法必须一致。ISP 经芯片 ROM 串口引导程序写入 Flash；SWD 则通过独立 ST-Link 下载器访问目标芯片，二者需分别验收。',
 'STM32F103 的 GPIOA 时钟由 RCC_APB2ENR 的 IOPAEN 控制。CRL 为每个低位引脚分配 4 位配置，PA0/PA1 设为 2 MHz 推挽输出后，CRL 低 8 位为 0x22。BSRR 低 16 位用于置位，高 16 位用于复位，可一次写入同时切换两盏灯。本板 LED 低电平有效。',
 '标准外设库将寄存器操作封装为时钟、GPIO 初始化和输出函数。LED_Init 先使能时钟，将 PA0/PA1 预置高电平，再配置推挽输出。LED_Write(0,1) 输出低电平点亮 D1。Delay_Ms 以 SysTick 提供的毫秒计数计算无符号时间差，实现亮 1 s、灭 1 s。',
 'PA15 和 PA12 采用上拉输入，按下时读到低电平。PA15 原属 JTAG，需要关闭 JTAG 并保留 SWD。Keys_Poll 要求电平稳定 20 ms 才确认状态改变，仅在稳定按下时产生一次事件。K1 对两灯分别取反；K4 使用独立变量依次输出互斥状态，长按不会连续执行。',
 '将 PA15、PA12 映射到 EXTI15、EXTI12，配置下降沿触发并启用 EXTI15_10_IRQn。两条线共用处理函数，必须分别检查和清除挂起标志，不能用互斥的 else if 遗漏第二个中断源。中断只锁存事件和累计计数，主循环完成消抖及 LED 更新。',
 'USART1 使用 PA9 发送、PA10 接收，经底板 CH340 与 PC 通信。配置为 115200 bit/s、8 数据位、无校验、1 停止位。接收中断将数据写入 128 字节环形缓冲区，主循环只识别大写 Y 和 N；其他字符不改变 LED。问号用于读取辅助状态。',
 'SysTick 采用处理器时钟，72 MHz 时配置 1 ms 重装值为 72000000/1000−1=71999。中断令 g_millis 加 1；主循环比较无符号差 now−last 是否达到 1000，并执行 last+=1000。每秒翻转一次对应完整亮灭周期约 2 s；绝对精度仍由时钟和实际测量决定。',
 'K1/PA15 与 K4/PA12 通过 EXTI 锁存并在主循环消抖。K1 改变 blink_enabled，启动时立即点亮 D1，此后每 1000 ms 翻转，停止时立即熄灭。K4 请求 OLED 显示八个 16×16 汉字。PB10/PB11 以开漏软件 I²C 传输，1024 字节显存按页组织，每轮最多发送 16 字节，避免一次整屏刷新长时间占用主循环。',
]
RESULTS=[
 '2026 年 10 月 4 日已通过串口 ISP 下载并完整回读 64 KB 校验；实际串口收到 4900 字节，其中 70 条 STATE 报告 D1 状态交替。ST-Link 的 USB 可枚举，但目标 SWD 连接未成功。实体 LED 发光尚无观察记录，因此本实验为部分验证，不能填写两种下载方式均通过。',
 '已完成 ISP 下载和 64 KB 回读校验。串口记录报告 16 次状态翻转，PC 到达间隔均值 0.500970 s，范围 0.493832～0.507622 s。该记录反映程序日志和 PC 粗计时，没有直接测量 PA0/PA1 电压或两盏 LED 光学状态，实体交替亮灭仍需补测。',
 '已完成 ISP 下载和完整回读。串口记录报告 12 次翻转，PC 到达间隔均值 1.000002 s，范围 0.992949～1.005327 s；ON/OFF 意图日志符合程序周期。日志不能代替实体 D1 观察，也不能视为校准仪器测得的 1 s 精度。',
 '已完成 ISP 下载和完整回读，实际串口采集到 62336 字节，STATE 格式正确，RX_ERRORS 未增加。现有采集中没有足够的实体按键操作证据；K1 同时反转、K4 互斥交替、长按与同时按压已在离线状态机中通过，板上按键及 LED 现象仍待验收。',
 '工程编译、共享向量和离线中断逻辑检查通过，包括两条挂起位同时出现及按键抖动场景。当前没有对应固件的有效上板下载记录，也没有实体按键下降沿或 LED 观察证据。因此实验五仍为待上板验证。',
 '已完成 ISP 下载和 64 KB 回读。实际 CH340 串口发送 Y 后回复 OK Y LED0=1，发送 N 后回复 OK N LED0=0；X、小写 y 和回车换行不改变固件报告状态。串口通信和命令解析子项已验证；D1 实体发光及引脚电平仍无直接观察记录。',
 '已完成 ISP 下载和回读。12 条 STATE 的相邻目标毫秒差均为 1000；串口报告 12 次翻转，PC 到达间隔均值 1.000261 s，范围 0.995127～1.004575 s。配置和相对周期有证据，LED 光学观察及外部仪器测得的绝对时间误差尚未完成。',
 '2026 年 10 月 13 日 OLED 版已编译通过，新增 8 组专项离线测试全部通过，核对了地址探测、初始化、显存、按键与闪烁并行及总线故障退出。用户仅确认四针 I²C 接口，控制器与分辨率仍未确认；尚未将本版下载并观察实体 OLED，不能填写亮屏或中文显示实测通过。',
]
ANALYSIS=[
 '平台验证必须区分编译成功、Flash 写入校验和实际运行。ISP 成功可支持继续软件调试，但不能替代实验一要求的 ST-Link 下载子项。',
 '寄存器操作需要把引脚位号、输出极性和寄存器写法逐项对应。低电平点亮使逻辑“亮”与 ODR 位值相反，判断时应避免直接将位值 1 解释为灯亮。',
 '库函数提升可读性，但仍依赖正确的时钟与 GPIO 配置。比较实验二、三可以把库调用还原到寄存器含义，便于定位移植中的引脚或极性错误。',
 '消抖应基于稳定状态和按下事件，避免用长延时阻塞整个循环。互斥状态应单独保存，才不会被另一按键的反转操作打乱规则。',
 '共享中断入口的两个源需要独立处理。机械抖动可能让原始 IRQ 计数多次增加，而业务动作经过消抖后仍应只执行一次，二者并不矛盾。',
 '排查串口应先确认端口、波特率和接收字节，再确认命令判断和输出极性。断点可能造成缓冲区溢出，正常通信验证应在全速运行状态下进行。',
 '无符号差值能处理毫秒计数回绕；last+=1000 保持相对周期安排。绝对计时精度必须用独立仪器测量，不能由同一 g_millis 计数自证。',
 '显示更新放在主循环，ISR 不执行整屏传输。地址 ACK 仅说明设备响应，不能识别控制器或证明画面正确；有限超时和分块发送使显示故障不会无限阻塞按键与 LED。',
]

def code_block(g,text):
    lines=[]
    for line in text.splitlines():
        lines.extend(textwrap.wrap(line,width=94,subsequent_indent='    ',replace_whitespace=False,
                                   drop_whitespace=False) or [''])
    p=g.p('\n'.join(lines),size=8.5)
    p.paragraph_format.line_spacing=1.0
    p.paragraph_format.space_after=Pt(4)
    p.paragraph_format.widow_control=False
    for run in p.runs:run.font.name='Consolas'

def build():
    # Evidence is read, never generated or upgraded into physical observations.
    compile_data=json.loads((ROOT/'Build/keil_results.json').read_text())
    oled=json.loads((ROOT/'Build/oled_logic_check_results.json').read_text())
    logic=json.loads((ROOT/'Build/logic_check_results.json').read_text())
    assert len(compile_data)==8 and all(x['passed'] for x in compile_data)
    assert oled['passed'] and all(x['passed'] for x in logic['tests'])
    assert oled['axf_sha256']==hashlib.sha256((ROOT/'Build/Keil_08_Application/08_Application.axf').read_bytes()).hexdigest()
    g=Guide()
    g.doc.core_properties.title='嵌入式系统实验报告 STM32F103 OLED 整合版'
    g.doc.core_properties.subject='八项实验原理 程序 调试和验证结果'
    footer=g.doc.sections[0].footer.paragraphs[0]
    for run in footer.runs:
        if '补充指南' in run.text:run.text='STM32F103 嵌入式系统实验报告  |  '
    g.page('嵌入式系统实验报告',first=True)
    g.p('STM32F103 完整开发板  OLED 综合实验',bold=True,size=14)
    g.p('姓名 ____________    学号 ____________________\n班级 ____________    指导教师 ________________')
    g.p('报告整理日期  2026 年 10 月 13 日\n板上记录日期  2026 年 10 月 4 日    软件更新日期  2026 年 10 月 13 日')
    g.h('实验概述')
    g.p('本报告按照《嵌入式系统实验指导书》的八项任务，整合 GPIO 寄存器、标准外设库、按键、EXTI、USART 和 SysTick 的程序与调试结果。硬件统一为普中精灵 STM32F103C8T6 完整开发板；实验八改用四针 I²C OLED 显示“嵌入式实验完成啦”。所有关键源码以当前 Keil 工程为准。')
    g.p('八个目标均编译通过，原有 8 组逻辑回归和新增 8 组 OLED 离线测试通过。实验 1、2、3、4、6、7 已有部分真实 ISP 或串口证据；实验 5、8 尚无完整上板验证。报告据此保留未验证项，不将软件模型或预期现象记作实测结果。')
    g.table(['实验','任务','当前板上证据'],[
      ('一','平台与两种下载方式','ISP 和串口部分通过；SWD 待补测'),
      ('二','寄存器控制两灯交替','下载及日志通过；两灯观察待测'),
      ('三','标准库控制一秒亮灭','下载及日志通过；灯光观察待测'),
      ('四','轮询按键与消抖','下载及串口采集通过；实体操作待测'),
      ('五','两路 EXTI 控制 LED','当前仅离线通过；上板待测'),
      ('六','串口 Y 和 N 命令','实际命令及固件报告通过；灯光待测'),
      ('七','SysTick 毫秒节拍','相对周期有记录；绝对精度待测'),
      ('八','按键闪烁与 OLED 中文','OLED 离线通过；型号及实屏待确认'),
    ],[1.1,6.1,9.8])
    g.p('完整源码见 F103_Labs 目录；八个 main.c 原文列在附录。姓名、学号及现场验收信息由实际操作者填写。')

    g.page('实验环境与硬件连接')
    g.p('实验 1 至 7 使用底板现有 D1、D2、K1、K4，无需再外接第二只 LED 或按键。实验八需外接 OLED。核心板保持插在底板上；改接线和跳帽前先断开底板 USB 与 ST-Link USB。')
    g.table(['设备或软件','配置与位置'],[
     ('开发板','普中精灵 STM32F103C8T6 完整板；64 KB Flash，20 KB SRAM；8 MHz HSE，PLL×9，HCLK 72 MHz'),
     ('Keil MDK','uVision 5.43.1；Arm Compiler 6.24；D:\\Keil5\\UV4\\UV4.exe'),
     ('器件包与库','Keil STM32F1xx_DFP 2.4.1；工程内含 STM32F1 SPL 和 CMSIS 5.9'),
     ('串口与 ISP','Tera Term：D:\\TeraTerm\\ttermpro.exe；FlyMCU：D:\\FlyMCU\\FlyMcu.exe'),
     ('辅助校验','D:\\F411_PythonEnv\\Scripts\\python.exe；名称沿用旧环境，校验对象为当前 F103'),
     ('OLED 假设','已确认 GND、VCC、SCL/SCK、SDA 四针 I²C；驱动暂按 SSD1306 128×64，需核对实物型号'),
    ],[3.1,13.9])
    g.table(['用途','引脚或跳线','配置'],[
     ('D1 / D2','PA0 / PA1','低电平亮；J11 短接启用 LED'),
     ('K1 / K4','PA15 / PA12','上拉输入、按下为低；K2/PA14 与 K3/PA13 留给 SWD'),
     ('USART1','PA9 TX / PA10 RX','P4 短接 A9–UR、A10–UT；底板 CH340 USB 提供串口'),
     ('ST-Link','DIO→PA13；CLK→PA14；GND→GND','有专用 VTref 检测脚时按下载器说明接目标 3.3 V'),
     ('OLED 电源','GND→GND；VCC→3V3','确认模块支持 3.3 V；不得按针脚位置猜测'),
     ('OLED 信号','SCL/SCK→PB10；SDA→PB11','从 GPIO 标名引出；开漏总线需要上拉至 3.3 V'),
     ('运行设置','BOOT0/B0=0；BOOT1/B1=0','J1 置 VCC 侧禁用点阵；不操作矩阵键盘'),
    ],[2.2,6.2,8.6])
    g.p('底板供电 USB 位于 CH340 附近，不要把核心板自身 USB 当作本工程的串口。底板已有供电时，不并接下载器的 3.3 V/5 V 电源输出。底板 OLED 插座的针序和网络尚未核实，本方案按 GPIO 名称接线。')

    g.page('工程结构与公共调试流程')
    g.p('工程入口为 D:\\MDK5\\F103_Labs\\MDK\\F103_Labs.uvprojx。每个 Target 只编译自身的 main.c；OLED 驱动仅加入 08_Application。每次下载会替换当前运行的实验。')
    g.table(['目录','作用'],[
      ('App','platform.c 提供时钟、LED、按键、消抖、中断及串口；system_lab.c 完成系统时钟设置'),
      ('Labs','01_Platform 至 08_Application；每目录独立 main.c；实验八另含 OLED 驱动和字模'),
      ('Vendor 与 MDK','CMSIS、启动文件、STM32F1 SPL，以及项目、选项和 flash.sct 链接配置'),
      ('Build 与 tools','AXF、HEX、BIN、构建和离线结果；编译、校验、串口和下载辅助脚本'),
      ('Evidence 与 Backup','按日期保存实际通信和离线记录；原始固件、更新前工程及旧 PC 显示源码'),
    ],[3.1,13.9])
    g.steps([
      '打开 Keil，选择本实验 Target，执行 Project → Rebuild all target files。确认 0 Error(s)、0 Warning(s)，下载对应 Build\\Keil_<Target>\\<Target>.hex，不能混用其他实验产物。',
      '使用 ST-Link 时，在 Options for Target → Debug 选择 ST-Link Debugger，Settings 内选 SW。先确认探针和目标可识别，再执行下载与校验；当前历史 SWD 目标连接失败，不能把探针 USB 可见当作已连接芯片。',
      '手动串口 ISP：关闭占用 COM 口的终端，BOOT0=1、BOOT1=0，复位后用 FlyMCU 打开当前 COM 口和对应 HEX，下载并校验。结束后恢复 BOOT0=0 并复位。自动 ISP 脚本依赖本完整板复位电路，不能与手动跳帽流程混用。',
      'Tera Term 使用设备管理器当前 COM 号，设置 115200、8N1、无流控、UTF-8。历史端口为 COM4；打开终端后复位，核对 LAB 编号和 HCLK=72000000，再操作按键或发送命令。',
      '只有 SWD 连通后才进入 Debug 会话，设置断点并观察寄存器和变量。清除断点、全速运行后记录灯光和时间；暂停或单步会影响时序，不能用于正常周期验收。',
    ])
    g.p('首次写入前已完成两次一致的 64 KB 原始 Flash 备份。原程序 SHA256 为 9d569e132fe0df839dcbf3128d082ab570e8bdb1fb3aa84e006e333878add0dd，文件位于 Backup/ISP_35ff6f064d58303838561543/。备份仅用于恢复，不作为实验代码提交结果。')

    for i,lab in enumerate(LABS):
      title=lab['title'] if i!=7 else '实验八 综合控制与 OLED 中文显示'
      g.page(title)
      g.h('实验目的与原理')
      g.p(lab['purpose'] if i!=7 else '综合 GPIO、EXTI 和 SysTick，实现 K1 启停 D1 一秒翻转，K4 中断触发 OLED 中文显示；USART 保留辅助状态输出。')
      g.p(PRINCIPLES[i])
      g.h('程序与调试步骤')
      steps=lab['steps'] if i!=7 else [
        '按公共接线表接好 OLED，核对 SSD1306 128×64 型号假设后，选择 08_Application，重新编译并下载。正常运行设置为 BOOT0=0、BOOT1=0。',
        '复位后预期 D1 熄灭。OLED 显示 LAB8 OLED READY、BLINK: OFF 和 K1:BLINK K4:MSG；第三行初始为空。先确认初屏完整，再执行按键检查。',
        '短按 K1，D1 立即点亮，状态行变为 BLINK: ON，此后每 1 s 翻转。在闪烁期间短按 K4，第三行应显示“嵌入式实验完成啦”，闪烁继续。',
        '长按 K4 至少 1 s，只产生一次业务事件。再次按 K1 后 D1 熄灭并保持，状态行变 OFF，中文保留。重复三轮，检查同时按压和长按行为。',
        'SWD 连通时观察 g_oled_present、g_oled_address、g_oled_error_count 和 g_oled_frame_count。保存实体屏照片及按键和 D1 视频；串口中文仅作辅助，不能作为 OLED 显示通过的依据。',
      ]
      g.steps(steps)
      g.p(f'源码位置：Labs/{lab["target"]}/main.c。完整主程序见附录；公共函数见 App/platform.c。',size=9)
      g.h('实验结果与分析')
      g.p(RESULTS[i])
      g.h('问题分析与总结')
      g.p(ANALYSIS[i])

    g.page('OLED 实现与故障定位')
    g.p('OLED 通过 PB10/PB11 发送 I²C 数据，依次探测七位地址 0x3C 和 0x3D。SSD1306 以 0x00 接收命令、以 0x40 接收显存数据。显存分为 8 页，每页 128 字节；每次 OLED_Task 发送 16 字节，并单独定位页和列。首帧 1024 字节发送完成后才执行开屏命令。')
    g.doc.add_picture(str(ROOT/'Build/oled_font_preview.png'),width=Cm(15.8))
    g.p('图 1  中文字模和界面布局的软件预览  非实体屏幕照片',size=9)
    g.p('oled_font.h 保存 95 个英文字符和八个汉字的点阵；“嵌入式实验完成啦”按每字 16×16 排列，占 128×16 像素。SSD1306 接收的是点阵字节，不能把 UTF-8 字符串直接写入显存当作汉字。')
    g.table(['现象','检查重点'],[
      ('黑屏且初始化失败','确认 VCC=3.3 V、共地、PB10/PB11、上拉及 SCL/SDA 是否接反；断电修线后复位'),
      ('地址有应答但无字或偏移','核对控制器和分辨率；SH1106 或 128×32 不能仅凭四针外形认定兼容'),
      ('刷新途中停止','查看错误计数；故障后驱动置离线，不无限重试；修线后复位重新初始化'),
      ('K1 或 K4 无响应','确认 PA15/PA12 和对应按键，释放 JTAG 保留 SWD；检查程序是否停在断点'),
      ('PC 有中文而 OLED 黑屏','仅证明辅助串口日志执行；继续检查屏幕，不填写 OLED 实测通过'),
    ],[5.1,11.9])
    g.p('缺屏、NACK 或 SCL/SDA 卡低均已在离线模型中验证可以退出显示通信，并继续处理按键和闪烁。实际供电、上拉电阻、波形与画面方向仍需接板检查。')

    g.page('验证方法与结果追溯')
    g.p('本次整合没有新增硬件下载或观察记录。2026 年 10 月 13 日的 OLED 更新属于软件版本更新；实验一至七 HEX 与更新前逐字节相同，因此可继续引用 10 月 4 日的对应固件证据。实验八固件已变更，旧 PC 显示版本不能作为 OLED 验收证据。')
    g.table(['检查','结果','证据位置'],[
      ('Keil 全量编译','8 个目标通过，0 错误 0 警告','Build/keil_results.json'),
      ('工程与链接检查','8 个目标通过；存储边界、向量、BIN 与 HEX 一致','Build/project_check_results.json'),
      ('基础离线回归','8 组通过；包含消抖、共享 IRQ、串口和计时回绕','Build/logic_check_results.json'),
      ('OLED 专项离线测试','8 组通过；包括地址、显存、补帧和故障退出','Build/oled_logic_check_results.json'),
      ('未改实验的产物对比','实验 1 至 7 HEX 完全相同；实验 8 已更新','Build/oled_change_hex_comparison.json'),
      ('历史板上证据','ISP、原始通信及保守的分项汇总','Evidence/20261004/validation_summary.json'),
      ('OLED 版本记录','记录模型适配假设、文件哈希和未实测状态','Evidence/20261013/oled_offline_release.json'),
    ],[3.0,6.7,7.3])
    g.h('离线测试的范围')
    g.p('Unicorn 执行实际 Keil AXF，模型从 GPIO 写入解码 I²C 时钟、数据、ACK 和 SSD1306 显存，不替换 OLED 功能函数。仅跳过纯 CPU 延时函数 OLED_BusDelay；毫秒时钟与按键中断入口由测试注入。测试可证明程序在所覆盖条件下的逻辑行为，不能证明真实供电、电气时序、机械按键或实体像素。')
    g.h('实验总结')
    g.p('八项实验形成了从平台配置、GPIO 寄存器、标准库封装到事件和定时协作的程序结构。共享中断独立处理、稳定状态消抖、无符号计时以及分块显示，使各功能可以共同运行。现有工作已形成可复现源码和校验记录；仍需补齐 SWD 目标连接、实体按键、LED 和 OLED 观察，之后才能把相应项目标为完成。')
    g.p('原始任务依据：用户提供的《嵌入式系统实验指导书》2023 年修订版。引脚按当前普中完整板配置，未沿用旧 NUCLEO-F411RE 报告的端口、极性或实测数值。')

    g.page('现场补测与验收记录')
    g.p('下表供后续上板填写。保留实际日期、文件版本、连接和观察证据，不将预期现象直接填写为结果。实验一须分别记录 ISP 和 ST-Link；实验八须拍到实体 OLED 完整中文。')
    g.table(['实验','必须补充的现场项目','实测记录'],[
      ('一','ST-Link 下载校验及 D1 实体观察','日期 ________\n结果 ________'),
      ('二 三','D1/D2 交替及 D1 一秒亮灭','证据 ________\n结果 ________'),
      ('四','两键规则、长按、同时按压、两灯状态','证据 ________\n结果 ________'),
      ('五','下载、两路真实 EXTI 及共享入口','证据 ________\n结果 ________'),
      ('六','Y/N 命令与实体 D1 现象对应','证据 ________\n结果 ________'),
      ('七','实体翻转；如需精度则保存仪器波形','仪器 ________\n结果 ________'),
      ('八','型号、分辨率、初屏、K1/K4 及中文','型号 ________\n结果 ________'),
    ],[1.6,9.1,6.3])
    g.h('统一记录')
    g.p('操作人员 __________________  实验日期 __________________\n\n开发板及 OLED 型号 ____________________________________\n\nHEX 文件与 SHA256 _____________________________________\n\n连接方式及当前 COM 口 __________________________________\n\n照片或视频保存位置 ____________________________________\n\n未通过项和处理措施 ____________________________________\n\n复测结果及教师确认 ____________________________________')
    g.p('辅助说明保留在《实验八 OLED 接线与调试流程》中；完整工程包含 oled.c、oled.h、oled_font.h、公共驱动和厂商库，附录列出各实验当前主程序。')

    for i,lab in enumerate(LABS,1):
      source=ROOT/'Labs'/lab['target']/'main.c'
      g.page(f'附录 {i}  {lab["target"]} 主程序')
      g.p(f'文件 Labs/{lab["target"]}/main.c  以下为本次整合工程的完整文件内容。',size=9)
      code_block(g,source.read_text(encoding='utf-8'))
      if i==8:
        g.p('oled.c 实现总线与分块刷新，oled_font.h 提供点阵。工程包含完整文件；Backup/20261013_lab8_PC_original_main.c 保留旧 PC 显示版主程序。',size=9)
    g.doc.save(OUTPUT)
    print(OUTPUT)

if __name__=='__main__':build()
