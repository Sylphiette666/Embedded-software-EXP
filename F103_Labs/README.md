# 普中精灵 STM32F103C8T6 完整开发板实验工程

## 2026年10月13日 实验八改用 OLED
实验八现使用四针 I²C OLED；实验1—7的HEX与更新前逐字节相同。
当前屏幕接口已由用户确认，控制芯片和分辨率尚未确认；固件按SSD1306、128×64、3.3V模块适配。
SH1106、128×32或其他控制器不能仅凭四针外形认定兼容。

断开底板和ST-Link的USB后接线，核心板保留在底板上：OLED GND→GND、VCC→3V3、SCL/SCK→板上SCL（PB6）、SDA→板上SDA（PB7）。
2026-10-13按普中官方OLED插座原理图修正为PB6/PB7，与用户现有SCL/SDA接线对应；旧版PB10/PB11接线说明不再适用。
依据：https://blog.csdn.net/2501_92850798/article/details/150758386 的硬件设计原理图（正文将PB6/PB7顺序写反，以原理图和第34章EEPROM说明为准）。
SCL/SDA采用开漏输出，需要上拉至3.3V（通常模块已带上拉）；本实验不使用矩阵键盘。
首次正常刷新后显示LAB8 OLED READY、BLINK: OFF和K1:BLINK K4:MSG。
K1启停D1，启动后每1000ms翻转；K4通过EXTI事件在主循环显示“嵌入式实验完成啦”。

驱动探测七位地址0x3C/0x3D，每轮最多发送16个显存数据字节。无应答或总线卡低会退出显示通信，按键与LED逻辑继续运行；修线后按RESET重新检测。
ACK不能识别控制器，也不能证明画面正确。串口中文仅为辅助日志，必须观察实体OLED验收。
2026-10-13已补入三张实物照片，确认亮屏、BLINK ON/OFF状态和完整中文显示；操作者已确认八项实验均完成验收。照片位于docs/images/20261013_oled/。

源码：Labs/08_Application/main.c、oled.c、oled.h、oled_font.h。Keil目标仍为08_Application。
OLED离线模型测试入口：tools/check_oled_logic.py；结果写入Build/oled_logic_check_results.json。
2026-10-13新增OLED八组离线测试全部通过：两个地址、显存分块及开屏、帧中更新、按键和闪烁并行、无屏NACK、SCL/SDA卡低及刷新中故障。原八组固件逻辑回归也全部通过。
测试执行实际AXF并解码GPIOB通信，只跳过纯CPU延时；模型注入时间和按键中断，不能代替实体屏幕、电气波形或真实计时验收。
工程、旧PC显示版源码及旧编译产物已备份：Backup/20261013_before_oled.zip。
旧Word补充指南中的PB10/PB11接线已过时，请以本README的板载插座接线为准。文档路径：D:\Work\嵌入式实验\实验八_OLED接线与调试流程_20261013.docx。
旧20261004完整指南的实验1—7流程继续适用；其旧实验八PC显示章节由本次补充指南替代。

## 入口
- Keil 工程：D:\MDK5\F103_Labs\MDK\F103_Labs.uvprojx
- 本次 Word：D:\Work\嵌入式实验\普中精灵F103_上板验证与源码调试流程_20261004.docx
- 先前调试指南保留：D:\Work\嵌入式实验\普中精灵F103_八项实验详细调试流程.docx
- 桌面“STM32实验工具”已切换至 D:\F103_LabTools\启动入口。
- F411 原工程保留在 D:\MDK5\F411_Labs，其旧文档不适用于本板。

## 本板接线
LED0=底板D1/PA0，LED1=底板D2/PA1，均低电平亮。
KEY0=K1/PA15，KEY1=K4/PA12，均按下接地、上拉输入。
K2/PA14 和 K3/PA13 留给 SWD，不参与本工程。
板载 USART1：PA9 TX、PA10 RX；P4 短接 A9–UR、A10–UT。
底板 USB1 为 CH340 USB 转串口，核心板 USB 口不是本工程串口口。
J11 短接以启用 LED；J1 接 VCC 侧禁用点阵输出。改跳线前先断电。
正常运行 BOOT0=0、BOOT1=0。USB1 供电时，下载器按其说明连接电压参考，避免再接另一电源输出。

## 软件
Keil 5.43.1 / Arm Compiler 6.24；F1 DFP 2.4.1 已安装。
工程随附 STM32F1 SPL 和 CMSIS 5.9，时钟采用 ST 系统文件：8 MHz HSE、PLL x9、HCLK 72 MHz、APB1 36 MHz。
Tera Term：D:\TeraTerm\ttermpro.exe，115200/8N1/无流控/UTF-8。

2026-10-13 串口实测：CH340 当前为 COM6。Tera Term 除 115200/8N1/无流控外，还需将 FlowCtrlDTR=0、FlowCtrlRTS=0（禁用）保存到配置中；仅选择“无流控”仍可能使控制线干扰本板启动。已验证配置：tools/teraterm_lab6.ini。实验六在该配置下返回启动信息、OK Y LED0=1、OK N LED0=0 和 STATE，RX_ERRORS=0。更换 USB 接口后请重新确认 COM 号。

FlyMCU：D:\FlyMCU\FlyMcu.exe，用于串口 ISP。串口需先从 Tera Term 断开。
CH340 驱动已在 Windows 驱动库中：oem46.inf，3.9.2024.9，微软有效签名；备份 D:\CH340_Driver\Installed_Driver_Backup。
复用已安装的 D:\F411_PythonEnv\Scripts\python.exe 进行离线校验及板上验证，目录名沿用旧名，操作对象已改为 F103。
STM32CubeProgrammer 尚未安装（ST 官网下载未成功），此 SPL 工程可以用 Keil/ST-Link 和 FlyMCU/ISP 完成，无需 CubeMX、CubeIDE 或 Proteus。

## 编译与下载
在 Keil 左上 Target 选择 01 至 08 对应实验，Project → Rebuild all target files。
输出：Build\Keil_<Target>\<Target>.hex。每个 HEX 仅对应一个实验。
ST-Link 为默认调试器，端口 SW，时钟 1 MHz；算法 STM32F10x_128.FLM 支持中容量器件，工程限制仍为64 KB。
若实际下载器是 CMSIS-DAP，需要在 Debug 和 Utilities 中选择 CMSIS-DAP 并配置 SWD；它不能代替指导书中点名要求的 ST-Link 验收。
FlyMCU 手动 ISP：BOOT0=1、BOOT1=0，复位后下载 HEX，校验成功，再恢复 BOOT0=0 并复位。
2026-10-04 已通过底板 CH340/COM4 对实验 1、2、3、4、6、7 执行串口 ISP 下载，并完整读取 64 KB 校验。每次下载会替换当前运行的实验。早期 SWD 连接问题已解决；操作者随后确认包括 ST-Link 在内的八项实验均已完成验收。

首次下载前已通过 ISP 连读两次 64 KB，逐字节一致，原程序备份为 Backup\ISP_35ff6f064d58303838561543\20261004_161325_932217_double_read_backup_08000000.bin，SHA256 为 9d569e132fe0df839dcbf3128d082ab570e8bdb1fb3aa84e006e333878add0dd。后续 preflash 文件可能包含中间实验程序，不能用作最初固件的替代。

## 验证状态
八个目标 Keil 全量编译均为0错误0警告，内存范围与中断向量检查通过。
八组软件回归通过：实际AXF在Unicorn中执行，GPIO/UART为模型、时间与中断由测试注入。
实验2/3检查初始化输出；实验1/7检查周期输出；不代替晶振、真实中断时序、USB、下载器和板上按键实测。
记录位于 Build/keil_results.json、project_check_results.json、logic_check_results.json。
真实板上记录另存 Evidence/20261004，当前汇总为 validation_summary.json。串口报告、实体按键操作、LED 实物观察与 SWD 读回分别记录；最新现场验收状态由操作者确认：八项均已完成验收；历史原始记录保持原样。
tools/bench_isp.py 用于本完整板的串口识别、备份、下载和回读；tools/bench_serial.py 只采集实际串口；tools/bench_swd.py 用于指定探针的 SWD 验证；tools/summarize_evidence.py 仅读取证据生成汇总。运行任何下载前应关闭占用 COM 口的终端，并按各工具 --help 确认参数。
只编译及校验：powershell -ExecutionPolicy Bypass -File D:\F103_LabTools\check_environment.ps1
打开串口：powershell -ExecutionPolicy Bypass -File D:\F103_LabTools\open_serial.ps1
多个串口时在命令末尾加 -Port COMn，按设备管理器实际端口填写。

## 八个实验
01_Platform：D1 每500 ms翻转，串口输出启动信息及状态。
02_Register：寄存器控制 D1/D2 交替，每500 ms换向。
03_StdPeriph：标准库控制 D1 亮1 s、灭1 s。
04_Keys：K1同时反转两灯；K4依次产生10、01互斥状态。
05_EXTI：K1中断翻转D1，K4中断翻转D2，共用EXTI15_10入口。
06_USART：大写Y开D1、大写N关D1；其他字符不改变LED，?附加查询状态。
07_SysTick：1 ms节拍，D1每1000 ms翻转（完整亮灭周期2 s）。
08_Application：K1启停D1闪烁；K4中断触发OLED显示“嵌入式实验完成啦”，串口仅作辅助。

## 资料依据
用户实验指导书及课设中保存的普中精灵完整板商品图。
普中官方板卡使用说明：https://blog.csdn.net/2501_92850798/article/details/149668892
普中官方按键说明：https://blog.csdn.net/2501_92850798/article/details/150204565
普中官方点阵与J1/J11说明：https://blog.csdn.net/2501_92850798/article/details/150260376
CMSIS 5.9：https://github.com/ARM-software/CMSIS_5/tree/5.9.0
STM32F103x8/xB数据手册：https://www.st.com/resource/en/datasheet/stm32f103c8.pdf
SSD1306控制器数据手册：https://cdn.sparkfun.com/assets/d/a/a/b/c/SSD1306_datasheet.pdf
