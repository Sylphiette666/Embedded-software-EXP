#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); Keys_Init(); UART_Init();
    Print_Banner(7, "SysTick 1 ms; LED toggles every 1000 ms");
    uint32_t last = Time_Now();
    while (1) {
        uint32_t now = Time_Now();
        if ((uint32_t)(now - last) >= 1000u) {
            last += 1000u; LED_Toggle(0); Print_State();
        }
        if (UART_GetChar() == '?') { Print_State(); }
    }
}
