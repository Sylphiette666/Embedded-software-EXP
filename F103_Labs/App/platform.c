#include "platform.h"

volatile uint32_t g_millis;
volatile uint32_t g_key0_irq_count;
volatile uint32_t g_key1_irq_count;
volatile uint32_t g_uart_overruns;
static volatile uint8_t irq_latched;
static volatile uint8_t rx_head, rx_tail;
static volatile uint8_t rx_buffer[128];
typedef struct { uint8_t raw, stable; uint32_t changed; } KeyFilter;
static KeyFilter keys[2];

void Time_Init(void)
{
    SystemCoreClockUpdate();
    if (SysTick_Config(SystemCoreClock / 1000u) != 0u) {
        while (1) {}
    }
    NVIC_SetPriority(SysTick_IRQn, 3u);
}
void SysTick_Handler(void) { ++g_millis; }
uint32_t Time_Now(void) { return g_millis; }
void Delay_Ms(uint32_t ms)
{
    uint32_t start = Time_Now();
    while ((uint32_t)(Time_Now() - start) < ms) { __NOP(); }
}
void LED_Init(void)
{
    GPIO_InitTypeDef gpio;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
    GPIO_SetBits(GPIOA, LED0_PIN | LED1_PIN); /* Active low, initially off. */
    GPIO_StructInit(&gpio);
    gpio.GPIO_Pin = LED0_PIN | LED1_PIN;
    gpio.GPIO_Mode = GPIO_Mode_Out_PP;
    gpio.GPIO_Speed = GPIO_Speed_2MHz;
    GPIO_Init(GPIOA, &gpio);
}
void LED_Write(uint8_t index, uint8_t on)
{
    uint16_t pin = index == 0u ? LED0_PIN : LED1_PIN;
    GPIO_WriteBit(GPIOA, pin, on ? Bit_RESET : Bit_SET);
}
void LED_Toggle(uint8_t index)
{
    LED_Write(index, (uint8_t)!LED_Read(index));
}
uint8_t LED_Read(uint8_t index)
{
    return (uint8_t)(GPIO_ReadOutputDataBit(GPIOA, index == 0u ? LED0_PIN : LED1_PIN) == Bit_RESET);
}
uint8_t Key_IsDown(uint8_t index)
{
    return (uint8_t)(index == 0u ?
        GPIO_ReadInputDataBit(GPIOA, GPIO_Pin_15) == Bit_RESET :
        GPIO_ReadInputDataBit(GPIOA, GPIO_Pin_12) == Bit_RESET);
}
void Keys_Init(void)
{
    GPIO_InitTypeDef gpio;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_AFIO, ENABLE);
    /* Release PA15 from JTAG, retain SWD on PA13/PA14. */
    GPIO_PinRemapConfig(GPIO_Remap_SWJ_JTAGDisable, ENABLE);
    GPIO_StructInit(&gpio);
    gpio.GPIO_Mode = GPIO_Mode_IPU;
    gpio.GPIO_Pin = GPIO_Pin_15 | GPIO_Pin_12;
    GPIO_Init(GPIOA, &gpio);
    for (uint8_t i = 0u; i < 2u; ++i) {
        keys[i].raw = Key_IsDown(i);
        keys[i].stable = 0u;
        keys[i].changed = Time_Now();
    }
}
uint8_t Keys_Poll(void)
{
    uint8_t events = 0u;
    uint32_t now = Time_Now();
    for (uint8_t i = 0u; i < 2u; ++i) {
        uint8_t down = Key_IsDown(i);
        if (down != keys[i].raw) {
            keys[i].raw = down;
            keys[i].changed = now;
        }
        if (keys[i].stable != down && (uint32_t)(now - keys[i].changed) >= 20u) {
            keys[i].stable = down;
            if (down) { events |= (uint8_t)(1u << i); }
        }
    }
    return events;
}
void Keys_EnableEXTI(uint8_t mask)
{
    EXTI_InitTypeDef exti;
    NVIC_InitTypeDef nvic;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_AFIO, ENABLE);
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_2);
    EXTI_StructInit(&exti);
    exti.EXTI_Mode = EXTI_Mode_Interrupt;
    exti.EXTI_Trigger = EXTI_Trigger_Falling;
    exti.EXTI_LineCmd = ENABLE;
    nvic.NVIC_IRQChannelPreemptionPriority = 1u;
    nvic.NVIC_IRQChannelSubPriority = 0u;
    nvic.NVIC_IRQChannelCmd = ENABLE;
    if (mask & KEY0_EVENT) {
        GPIO_EXTILineConfig(GPIO_PortSourceGPIOA, GPIO_PinSource15);
        exti.EXTI_Line = EXTI_Line15;
        EXTI_ClearITPendingBit(EXTI_Line15);
        EXTI_Init(&exti);
        nvic.NVIC_IRQChannel = EXTI15_10_IRQn;
        NVIC_Init(&nvic);
    }
    if (mask & KEY1_EVENT) {
        GPIO_EXTILineConfig(GPIO_PortSourceGPIOA, GPIO_PinSource12);
        exti.EXTI_Line = EXTI_Line12;
        EXTI_ClearITPendingBit(EXTI_Line12);
        EXTI_Init(&exti);
        nvic.NVIC_IRQChannel = EXTI15_10_IRQn;
        NVIC_Init(&nvic);
    }
}
void EXTI15_10_IRQHandler(void)
{
    if (EXTI_GetITStatus(EXTI_Line15) != RESET) {
        EXTI_ClearITPendingBit(EXTI_Line15);
        irq_latched |= KEY0_EVENT;
        ++g_key0_irq_count;
    }
    if (EXTI_GetITStatus(EXTI_Line12) != RESET) {
        EXTI_ClearITPendingBit(EXTI_Line12);
        irq_latched |= KEY1_EVENT;
        ++g_key1_irq_count;
    }
}
uint8_t Keys_PollIRQ(void)
{
    uint8_t pressed = Keys_Poll();
    uint32_t saved = __get_PRIMASK();
    __disable_irq();
    uint8_t events = pressed & irq_latched;
    irq_latched &= (uint8_t)~events;
    __set_PRIMASK(saved);
    return events;
}
void UART_Init(void)
{
    GPIO_InitTypeDef gpio;
    USART_InitTypeDef uart;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_USART1, ENABLE);
    GPIO_StructInit(&gpio);
    gpio.GPIO_Pin = GPIO_Pin_9;
    gpio.GPIO_Mode = GPIO_Mode_AF_PP;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &gpio);
    gpio.GPIO_Pin = GPIO_Pin_10;
    gpio.GPIO_Mode = GPIO_Mode_IN_FLOATING;
    GPIO_Init(GPIOA, &gpio);
    USART_StructInit(&uart);
    uart.USART_BaudRate = 115200u;
    uart.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
    USART_Init(USART1, &uart);
    USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
    NVIC_SetPriority(USART1_IRQn, 2u);
    NVIC_EnableIRQ(USART1_IRQn);
    USART_Cmd(USART1, ENABLE);
}
void USART1_IRQHandler(void)
{
    uint32_t status = USART1->SR;
    if (status & (USART_SR_RXNE | USART_SR_ORE | USART_SR_FE | USART_SR_NE | USART_SR_PE)) {
        uint8_t value = (uint8_t)USART1->DR; /* SR then DR clears errors. */
        if (status & (USART_SR_ORE | USART_SR_FE | USART_SR_NE | USART_SR_PE)) {
            ++g_uart_overruns;
        }
        if (status & USART_SR_RXNE) {
            uint8_t next = (uint8_t)((rx_head + 1u) & 127u);
            if (next != rx_tail) {
                rx_buffer[rx_head] = value;
                __DMB();
                rx_head = next;
            } else { ++g_uart_overruns; }
        }
    }
}
int UART_GetChar(void)
{
    if (rx_head == rx_tail) { return -1; }
    int value = rx_buffer[rx_tail];
    rx_tail = (uint8_t)((rx_tail + 1u) & 127u);
    return value;
}
void UART_Puts(const char *s)
{
    while (*s) {
        while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET) {}
        USART_SendData(USART1, (uint8_t)*s++);
    }
}
void UART_U32(uint32_t value)
{
    char buffer[11];
    uint8_t n = 0u;
    do { buffer[n++] = (char)('0' + value % 10u); value /= 10u; } while (value);
    while (n) {
        char s[2] = {buffer[--n], 0};
        UART_Puts(s);
    }
}
void Print_Banner(uint8_t lab, const char *title)
{
    UART_Puts("\r\nPUZHONG-JINGLING STM32F103C8T6 | LAB "); UART_U32(lab);
    UART_Puts(" | "); UART_Puts(title);
    UART_Puts("\r\nHCLK="); UART_U32(SystemCoreClock);
    UART_Puts("; USART1=115200 8N1; PA0=D1; PA1=D2; PA15=K1; PA12=K4\r\n");
    if (SystemCoreClock != 72000000u) { UART_Puts("WARNING: HSE/PLL not at 72 MHz; inspect crystal before timing acceptance.\r\n"); }
}
void Print_State(void)
{
    UART_Puts("STATE ms="); UART_U32(Time_Now());
    UART_Puts(" LED0="); UART_U32(LED_Read(0));
    UART_Puts(" LED1="); UART_U32(LED_Read(1));
    UART_Puts(" KEY0="); UART_U32(Key_IsDown(0));
    UART_Puts(" KEY1="); UART_U32(Key_IsDown(1));
    UART_Puts(" IRQ0="); UART_U32(g_key0_irq_count);
    UART_Puts(" IRQ1="); UART_U32(g_key1_irq_count);
    UART_Puts(" RX_ERRORS="); UART_U32(g_uart_overruns);
    UART_Puts("\r\n");
}
void Print_Completed(void)
{
    /* UTF-8 bytes: 嵌入式实验完成啦. Independent of editor source encoding. */
    UART_Puts("\xE5\xB5\x8C\xE5\x85\xA5\xE5\xBC\x8F\xE5\xAE\x9E"
              "\xE9\xAA\x8C\xE5\xAE\x8C\xE6\x88\x90\xE5\x95\xA6\r\n");
}
