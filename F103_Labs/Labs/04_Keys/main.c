#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); Keys_Init(); UART_Init();
    Print_Banner(4, "Polling keys with 20 ms debounce");
    UART_Puts("K1: invert both LEDs. K4: mutually exclusive LEDs.\r\n");
    uint8_t exclusive = 0;
    while (1) {
        uint8_t events = Keys_Poll();
        if (events & KEY0_EVENT) { LED_Toggle(0); LED_Toggle(1); }
        if (events & KEY1_EVENT) {
            exclusive ^= 1u;
            LED_Write(0, exclusive); LED_Write(1, (uint8_t)!exclusive);
        }
        if (events || UART_GetChar() == '?') { Print_State(); }
    }
}
