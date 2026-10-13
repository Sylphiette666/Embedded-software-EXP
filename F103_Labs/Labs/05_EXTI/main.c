#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); Keys_Init(); UART_Init();
    Keys_EnableEXTI(KEY0_EVENT | KEY1_EVENT);
    Print_Banner(5, "EXTI15 / EXTI12, deferred debounce");
    UART_Puts("K1 -> D1; K4 -> D2.\r\n");
    while (1) {
        uint8_t events = Keys_PollIRQ();
        if (events & KEY0_EVENT) { LED_Toggle(0); }
        if (events & KEY1_EVENT) { LED_Toggle(1); }
        if (events || UART_GetChar() == '?') { Print_State(); }
    }
}
