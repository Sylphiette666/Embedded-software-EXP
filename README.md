# 嵌入式实验

STM32F103C8T6 完整开发板八项实验。当前版本使用普中精灵底板，实验八采用四针 I²C OLED 显示中文。

## 从这里开始

- [完整实验报告 22 页](docs/嵌入式系统实验报告_STM32F103_OLED整合版_20261013.docx)：环境、八项原理与步骤、结果分析、待补测表和八个主程序源码。
- [OLED 接线与调试流程](docs/实验八_OLED接线与调试流程_20261013.docx)：软件、接线、下载、屏幕观察和排错。
- [当前 Keil 工程](F103_Labs/MDK/F103_Labs.uvprojx) 与 [工程说明](F103_Labs/README.md)。
- [完整源代码](F103_Labs/Labs)、[公共驱动](F103_Labs/App)、[构建产物](F103_Labs/Build) 和 [验证记录](F103_Labs/Evidence)。

## 硬件与程序

| 项目 | 当前配置 |
|---|---|
| MCU | STM32F103C8T6，64 KB Flash，20 KB SRAM |
| LED | D1=PA0，D2=PA1，低电平亮 |
| 按键 | K1=PA15，K4=PA12，低电平按下 |
| OLED | GND→GND，VCC→3V3，SCL/SCK→PB10，SDA→PB11 |
| 下载与串口 | 独立 ST-Link 的 SWD；底板 CH340 的 USART1/串口 ISP |
| 软件 | Keil MDK 5.43.1，Arm Compiler 6.24，STM32F1xx_DFP 2.4.1 |

OLED 接口已确认是四针 I²C，**控制器和分辨率尚未确认**。驱动暂按 SSD1306 128×64 适配，七位地址探测 0x3C/0x3D；四针或有 ACK 不等于确认型号。
改线前断开底板和下载器 USB，核心板留在底板上；按引脚丝印连接，不根据插座位置推断针序。SCL/SDA 需要上拉至 3.3 V。

| Target | 实验行为 |
|---|---|
| 01_Platform | D1 每 500 ms 翻转，平台与两种下载方式 |
| 02_Register | 寄存器控制 D1/D2 交替，每 500 ms 换向 |
| 03_StdPeriph | 标准库控制 D1 亮 1 s、灭 1 s |
| 04_Keys | K1 同时反转两灯，K4 产生互斥交替状态 |
| 05_EXTI | K1、K4 通过共享 EXTI 入口分别控制两灯 |
| 06_USART | 大写 Y 开 D1，大写 N 关 D1 |
| 07_SysTick | 1 ms 节拍，D1 每 1000 ms 翻转 |
| 08_Application | K1 启停闪烁，K4 在 OLED 显示“嵌入式实验完成啦” |

## 编译与验证

在 Keil 打开 `F103_Labs/MDK/F103_Labs.uvprojx`，选择一个 Target，重新编译后下载对应 HEX。每次写入会替换板上当前实验。
辅助脚本以自身路径定位工程；需要 Windows、Keil 及 `F103_Labs/tools/requirements.txt` 中的 Python 依赖。下列命令仅做构建与离线检查，不连接板卡：

```powershell
python F103_Labs/tools/build_keil.py --keil D:/Keil5/UV4/UV4.exe
python F103_Labs/tools/check_project.py
python F103_Labs/tools/check_firmware_logic.py
python F103_Labs/tools/check_oled_logic.py
```

2026-10-13：八个目标编译均为 0 错误、0 警告；工程/内存/向量及 BIN-HEX 一致性检查通过；基础 8 组与 OLED 专项 8 组离线测试通过。
模型执行实际 AXF，并模拟 GPIO、I²C、ACK、SSD1306 显存、时钟和按键事件。该结果不代替实体 LED、OLED、引脚波形或实际计时验收。

2026-10-04 的真实板上记录覆盖实验 1、2、3、4、6、7 的部分 ISP 下载、回读或串口检查。实验 5、8 尚无完整板上验证，ST-Link 目标连接仍需补测；本次报告整合没有新增烧写或实屏观察。
OLED 改动未改变实验 1 至 7 的 HEX。最新报告保留相应未测项，未将预期现象写成实测成功。

## 文件目录和历史资料

- `F103_Labs/`：当前工程、厂商库、源码、HEX/AXF/BIN、校验工具、实测原始记录和可恢复备份。
- `docs/`：本次整合报告和 OLED 操作指南；`reference/` 保存用户实验指导书。
- `docs/history/` 与 `archives/`：此前 F103 和 F411 的文档及交付包，保留作对照。
- `docs/images/`：已有 SWD 接线示意图及说明；该示意图不代表 OLED 插座针序。
- `tools/local_workflows/`：本机文档生成和环境入口脚本；其中安装路径沿用原电脑配置。
- `FILE_MANIFEST.json`：逐文件大小与 SHA256，可核对上传后的内容。

**旧 NUCLEO-F411RE 资料仅用于归档，不能套用其 PA5/PA6 引脚、有效电平或实测数值。**
2026-10-04 完整指南的实验八 PC 显示章节已由当前 OLED 版报告替代。
原 PC 版 main 保留在 `F103_Labs/Backup/20261013_lab8_PC_original_main.c`。

目录未收录临时锁文件、Python 虚拟环境、编辑器缓存、安装程序及账号凭据；源码依赖保留必要的厂商版权声明。
