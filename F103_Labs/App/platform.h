#ifndef LAB_PLATFORM_H
#define LAB_PLATFORM_H
#include "stm32f10x.h"
#include <stdint.h>

#define KEY0_EVENT 1u  /* K1, PA15, active low. */
#define KEY1_EVENT 2u  /* K4, PA12, active low. */
#define LED0_PIN GPIO_Pin_0
#define LED1_PIN GPIO_Pin_1

extern volatile uint32_t g_millis;
extern volatile uint32_t g_key0_irq_count;
extern volatile uint32_t g_key1_irq_count;
extern volatile uint32_t g_uart_overruns;
void Time_Init(void);
uint32_t Time_Now(void);
void Delay_Ms(uint32_t ms);
void LED_Init(void);
void LED_Write(uint8_t index, uint8_t on);
void LED_Toggle(uint8_t index);
uint8_t LED_Read(uint8_t index);
void Keys_Init(void);
void Keys_EnableEXTI(uint8_t mask);
uint8_t Keys_Poll(void);
uint8_t Keys_PollIRQ(void);
uint8_t Key_IsDown(uint8_t index);
void UART_Init(void);
void UART_Puts(const char *s);
void UART_U32(uint32_t value);
int UART_GetChar(void);
void Print_Banner(uint8_t lab, const char *title);
void Print_State(void);
void Print_Completed(void);
#endif
