#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); Keys_Init(); UART_Init();
    Print_Banner(6, "USART1 command control");
    UART_Puts("Y=D1 ON; N=D1 OFF; ?=state; other bytes ignored.\r\n");
    while (1) {
        int command = UART_GetChar();
        if (command == 'Y') { LED_Write(0, 1); UART_Puts("OK Y LED0=1\r\n"); }
        else if (command == 'N') { LED_Write(0, 0); UART_Puts("OK N LED0=0\r\n"); }
        else if (command == '?') { Print_State(); }
    }
}
