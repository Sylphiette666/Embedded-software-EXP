#include "platform.h"
int main(void)
{
    Time_Init(); LED_Init(); Keys_Init(); UART_Init();
    /* Both keys use EXTI, including the required second key. */
    Keys_EnableEXTI(KEY0_EVENT | KEY1_EVENT);
    Print_Banner(8, "Original two-key integrated experiment");
    UART_Puts("K1 toggles blink enable. K4 prints UTF-8 message.\r\n");
    uint8_t blink_enabled = 0u;
    uint32_t last = Time_Now();
    while (1) {
        uint8_t events = Keys_PollIRQ();
        uint32_t now = Time_Now();
        if (events & KEY0_EVENT) {
            blink_enabled ^= 1u; last = now;
            LED_Write(0, blink_enabled);
            UART_Puts(blink_enabled ? "BLINK ON\r\n" : "BLINK OFF\r\n");
        }
        if (events & KEY1_EVENT) { Print_Completed(); }
        if (blink_enabled && (uint32_t)(now - last) >= 1000u) {
            last += 1000u; LED_Toggle(0);
        }
        if (UART_GetChar() == '?') { Print_State(); }
    }
}
