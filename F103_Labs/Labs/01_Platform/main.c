#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); Keys_Init(); UART_Init();
    Print_Banner(1, "Platform and download smoke test");
    UART_Puts("D1 toggles every 500 ms. Send ? for state.\r\n");
    uint32_t last = Time_Now();
    while (1) {
        if ((uint32_t)(Time_Now() - last) >= 500u) {
            last += 500u; LED_Toggle(0);
        }
        if (UART_GetChar() == '?') { Print_State(); }
    }
}
