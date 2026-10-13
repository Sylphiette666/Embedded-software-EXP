/* Lab 2: LED GPIO init and control through F1 registers. */
#include "platform.h"
static void Register_LED_Init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    (void)RCC->APB2ENR;
    GPIOA->BSRR = 3u; /* PA0/PA1 high: both off. */
    GPIOA->CRL = (GPIOA->CRL & ~0xFFu) | 0x22u; /* 2 MHz push-pull. */
}
int main(void)
{
    Time_Init(); Register_LED_Init(); UART_Init();
    Print_Banner(2, "Register-driven alternating LEDs");
    while (1) {
        GPIOA->BSRR = (1u << 16) | (1u << 1);
        UART_Puts("LED0=1 LED1=0\r\n"); Delay_Ms(500);
        GPIOA->BSRR = (1u << 17) | 1u;
        UART_Puts("LED0=0 LED1=1\r\n"); Delay_Ms(500);
    }
}
