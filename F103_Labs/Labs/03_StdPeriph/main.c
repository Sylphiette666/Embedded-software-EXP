#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); UART_Init();
    Print_Banner(3, "StdPeriph LED: 1 second on, 1 second off");
    while (1) {
        LED_Write(0, 1); UART_Puts("ON\r\n"); Delay_Ms(1000);
        LED_Write(0, 0); UART_Puts("OFF\r\n"); Delay_Ms(1000);
    }
}
