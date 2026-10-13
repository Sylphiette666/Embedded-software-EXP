#ifndef LAB8_OLED_H
#define LAB8_OLED_H

#include <stdint.h>

/* Assumed module: SSD1306, 128x64, 4-wire I2C, 3.3 V.
 * PB10=SCL, PB11=SDA. Both pins are open-drain and require external
 * pull-ups to 3.3 V (normally already fitted on the OLED module).
 * This driver probes 7-bit address 0x3C, then 0x3D; no device-ID read is
 * available, so ACK alone does not establish the controller model.
 */
extern uint8_t g_oled_framebuffer[1024];
extern volatile uint8_t g_oled_present;
extern volatile uint8_t g_oled_address;
extern volatile uint32_t g_oled_error_count;
extern volatile uint32_t g_oled_frame_count;

/* Returns 1 after address ACK and initialization commands succeed.
 * Display pixels are sent later by OLED_Task; first completed frame turns
 * the display on. Returns 0 on absent device or a bus fault, without hanging.
 */
uint8_t OLED_Init(void);
uint8_t OLED_IsReady(void);
void OLED_SetBlink(uint8_t enabled);
void OLED_ShowCompleted(void);

/* Main-loop service only: at most 16 display-data bytes on each call,
 * plus bounded positioning/control commands. A full frame takes 64 calls.
 * Updates requested during a frame schedule another complete frame.
 * A transfer failure marks the display offline; reconnect then reset.
 */
void OLED_Task(void);

/* Pure bounded delay, intentionally public/noinline for offline emulation.
 * A simulator may skip this delay; it must still emulate the actual I2C
 * edges, input levels, ACK/NACK and bytes. Not a precision time source.
 */
void OLED_BusDelay(void);

#endif
