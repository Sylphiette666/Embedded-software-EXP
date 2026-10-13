/* SSD1306 software I2C, STM32F103 / standard peripheral library.
 * Protocol: Solomon Systech SSD1306 Rev 1.1, sections 8.1.5 and 10.
 * https://cdn.sparkfun.com/assets/d/a/a/b/c/SSD1306_datasheet.pdf
 *
 * No HAL, dynamic allocation, interrupt calls, or millisecond-tick waits.
 * This file only drives PB6/PB7 and the OLED framebuffer. Call its public
 * drawing/service functions from the main loop, never from an interrupt.
 */
#include "platform.h"
#include "oled.h"
#include "oled_font.h"

/* Puzhong Jingling OLED socket: SCL=PB6, SDA=PB7. */
#define OLED_SCL_PIN              GPIO_Pin_6
#define OLED_SDA_PIN              GPIO_Pin_7
#define OLED_LINE_WAIT_LIMIT      32u
#define OLED_POWER_SETTLE_STEPS   20000u
#define OLED_WIDTH                128u
#define OLED_FRAME_BYTES          1024u
#define OLED_CHUNK_BYTES          16u

uint8_t g_oled_framebuffer[OLED_FRAME_BYTES];
volatile uint8_t g_oled_present;
volatile uint8_t g_oled_address;
volatile uint32_t g_oled_error_count;
volatile uint32_t g_oled_frame_count;

static uint8_t oled_bus_fault;
static uint8_t oled_refresh_requested;
static uint8_t oled_refresh_active;
static uint8_t oled_display_on;
static uint16_t oled_refresh_offset;

__attribute__((noinline)) void OLED_BusDelay(void)
{
    uint32_t limit = SystemCoreClock / 1000000u;
    volatile uint32_t step;
    if (limit < 8u) { limit = 8u; }
    /* Several instructions per iteration give a conservative few-us delay
     * at the configured 8/72 MHz core clocks. Interrupts remain enabled.
     */
    for (step = 0u; step < limit; ++step) { __NOP(); }
}

static void SCL_Low(void)
{
    GPIO_ResetBits(GPIOB, OLED_SCL_PIN);
}

static void SDA_Low(void)
{
    GPIO_ResetBits(GPIOB, OLED_SDA_PIN);
}

static void SDA_Release(void)
{
    /* Open-drain ODR=1 releases the wire; it does not actively drive high. */
    GPIO_SetBits(GPIOB, OLED_SDA_PIN);
}

static uint8_t SDA_IsHigh(void)
{
    return GPIO_ReadInputDataBit(GPIOB, OLED_SDA_PIN) != Bit_RESET ? 1u : 0u;
}

static uint8_t SCL_ReleaseAndWait(void)
{
    uint32_t attempt;
    GPIO_SetBits(GPIOB, OLED_SCL_PIN);
    for (attempt = 0u; attempt < OLED_LINE_WAIT_LIMIT; ++attempt) {
        if (GPIO_ReadInputDataBit(GPIOB, OLED_SCL_PIN) != Bit_RESET) {
            return 1u;
        }
        OLED_BusDelay();
    }
    oled_bus_fault = 1u;
    return 0u;
}

static uint8_t I2C_Stop(void)
{
    uint8_t clock_ok;
    SCL_Low();
    SDA_Low();
    OLED_BusDelay();
    clock_ok = SCL_ReleaseAndWait();
    OLED_BusDelay();
    SDA_Release();
    OLED_BusDelay();
    if (clock_ok == 0u || SDA_IsHigh() == 0u) {
        oled_bus_fault = 1u;
        return 0u;
    }
    return 1u;
}

static uint8_t I2C_Start(void)
{
    SDA_Release();
    if (SCL_ReleaseAndWait() == 0u) { return 0u; }
    OLED_BusDelay();
    if (SDA_IsHigh() == 0u) {
        oled_bus_fault = 1u;
        return 0u;
    }
    SDA_Low();
    OLED_BusDelay();
    SCL_Low();
    OLED_BusDelay();
    return 1u;
}

static uint8_t I2C_WriteByte(uint8_t value)
{
    uint8_t bit;
    uint8_t released;
    uint8_t acknowledged;
    for (bit = 0u; bit < 8u; ++bit) {
        SCL_Low();
        released = (value & 0x80u) != 0u ? 1u : 0u;
        if (released != 0u) { SDA_Release(); } else { SDA_Low(); }
        OLED_BusDelay();
        if (SCL_ReleaseAndWait() == 0u) { return 0u; }
        OLED_BusDelay();
        /* Detect SDA stuck low / another driver when sending a logical 1.
         * ACK is sampled separately; the slave may pull SDA low there.
         */
        if (released != 0u && SDA_IsHigh() == 0u) {
            oled_bus_fault = 1u;
            SCL_Low();
            SDA_Release();
            return 0u;
        }
        SCL_Low();
        value = (uint8_t)(value << 1);
        OLED_BusDelay();
    }
    SDA_Release();
    OLED_BusDelay();
    if (SCL_ReleaseAndWait() == 0u) { return 0u; }
    OLED_BusDelay();
    acknowledged = SDA_IsHigh() == 0u ? 1u : 0u;
    SCL_Low();
    OLED_BusDelay();
    SDA_Release();
    return acknowledged;
}

static uint8_t I2C_Recover(void)
{
    uint8_t pulse;
    SDA_Release();
    if (SCL_ReleaseAndWait() == 0u) { return 0u; }
    OLED_BusDelay();
    if (SDA_IsHigh() == 0u) {
        /* A slave left mid-byte may release SDA after up to nine clocks.
         * Never wait indefinitely for SCL or SDA to become high.
         */
        for (pulse = 0u; pulse < 9u; ++pulse) {
            SCL_Low();
            OLED_BusDelay();
            if (SCL_ReleaseAndWait() == 0u) { return 0u; }
            OLED_BusDelay();
        }
    }
    return I2C_Stop();
}

static uint8_t I2C_Probe(uint8_t address)
{
    uint8_t acknowledged = 0u;
    uint8_t stopped;
    if (I2C_Start() != 0u) {
        acknowledged = I2C_WriteByte((uint8_t)(address << 1));
    }
    stopped = I2C_Stop();
    return acknowledged != 0u && stopped != 0u ? 1u : 0u;
}

static uint8_t I2C_Transmit(uint8_t control, const uint8_t *data, uint16_t length)
{
    uint16_t index;
    if (I2C_Start() == 0u) { (void)I2C_Stop(); return 0u; }
    if (I2C_WriteByte((uint8_t)(g_oled_address << 1)) == 0u ||
        I2C_WriteByte(control) == 0u) {
        (void)I2C_Stop();
        return 0u;
    }
    for (index = 0u; index < length; ++index) {
        if (I2C_WriteByte(data[index]) == 0u) {
            (void)I2C_Stop();
            return 0u;
        }
    }
    return I2C_Stop();
}

static void MarkOffline(void)
{
    g_oled_present = 0u;
    ++g_oled_error_count;
    oled_refresh_active = 0u;
    oled_refresh_requested = 0u;
    /* Leave both wires released even when the connected device holds one
     * low. Keep the last ACKed address for main-loop UART diagnostics.
     */
    GPIO_SetBits(GPIOB, OLED_SCL_PIN | OLED_SDA_PIN);
}

static void DrawTextRow(uint8_t row, const char *text)
{
    uint16_t base = (uint16_t)((uint16_t)row * 2u * OLED_WIDTH);
    uint16_t column;
    uint8_t character;
    uint8_t glyph_column;
    uint8_t character_index = 0u;
    if (row >= 4u) { return; }
    for (column = 0u; column < OLED_WIDTH * 2u; ++column) {
        g_oled_framebuffer[base + column] = 0u;
    }
    while (character_index < 16u && text[character_index] != '\0') {
        character = (uint8_t)text[character_index];
        if (character < 32u || character > 126u) { character = (uint8_t)'?'; }
        column = (uint16_t)((uint16_t)character_index * 8u);
        for (glyph_column = 0u; glyph_column < 8u; ++glyph_column) {
            g_oled_framebuffer[base + column + glyph_column] =
                oled_font_ascii[character - 32u][glyph_column];
            g_oled_framebuffer[base + OLED_WIDTH + column + glyph_column] =
                oled_font_ascii[character - 32u][8u + glyph_column];
        }
        ++character_index;
    }
    oled_refresh_requested = 1u;
}

uint8_t OLED_Init(void)
{
    /* Page addressing, 64 multiplex lines, internal charge pump.
     * Keep display OFF until all 1024 bytes of the first frame are sent,
     * avoiding briefly displaying undefined power-on GDDRAM contents.
     */
    static const uint8_t commands[] = {
        0xAEu, 0xD5u, 0x80u, 0xA8u, 0x3Fu, 0xD3u, 0x00u, 0x40u,
        0x8Du, 0x14u, 0x20u, 0x02u, 0xA1u, 0xC8u, 0xDAu, 0x12u,
        0x81u, 0x7Fu, 0xD9u, 0xF1u, 0xDBu, 0x20u, 0xA4u, 0xA6u, 0x2Eu
    };
    GPIO_InitTypeDef gpio;
    uint32_t step;
    uint16_t index;
    g_oled_present = 0u;
    g_oled_address = 0u;
    g_oled_error_count = 0u;
    g_oled_frame_count = 0u;
    oled_bus_fault = 0u;
    oled_display_on = 0u;
    oled_refresh_active = 0u;
    oled_refresh_requested = 0u;
    oled_refresh_offset = 0u;
    for (index = 0u; index < OLED_FRAME_BYTES; ++index) { g_oled_framebuffer[index] = 0u; }
    DrawTextRow(0u, "LAB8 OLED READY");
    DrawTextRow(1u, "BLINK: OFF");
    DrawTextRow(3u, "K1:BLINK K4:MSG");

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);
    GPIO_SetBits(GPIOB, OLED_SCL_PIN | OLED_SDA_PIN);
    gpio.GPIO_Pin = OLED_SCL_PIN | OLED_SDA_PIN;
    gpio.GPIO_Speed = GPIO_Speed_2MHz;
    gpio.GPIO_Mode = GPIO_Mode_Out_OD;
    GPIO_Init(GPIOB, &gpio);
    for (step = 0u; step < OLED_POWER_SETTLE_STEPS; ++step) { OLED_BusDelay(); }
    if (I2C_Recover() == 0u) { MarkOffline(); return 0u; }

    if (I2C_Probe(0x3Cu) != 0u) {
        g_oled_address = 0x3Cu;
    } else {
        /* An ordinary address NACK permits one alternative probe. An
         * electrical SCL/SDA fault does not permit further transactions.
         */
        if (oled_bus_fault != 0u) { MarkOffline(); return 0u; }
        if (I2C_Probe(0x3Du) == 0u) { MarkOffline(); return 0u; }
        g_oled_address = 0x3Du;
    }
    if (I2C_Transmit(0x00u, commands, (uint16_t)sizeof(commands)) == 0u) {
        MarkOffline();
        return 0u;
    }
    g_oled_present = 1u;
    return 1u;
}

uint8_t OLED_IsReady(void)
{
    return g_oled_present;
}

void OLED_SetBlink(uint8_t enabled)
{
    DrawTextRow(1u, enabled != 0u ? "BLINK: ON" : "BLINK: OFF");
}

void OLED_ShowCompleted(void)
{
    uint8_t character;
    uint8_t column;
    uint16_t base;
    /* Eight 16x16 glyphs fill y=32..47 across the complete 128-pixel row. */
    for (character = 0u; character < 8u; ++character) {
        base = (uint16_t)(4u * OLED_WIDTH + (uint16_t)character * 16u);
        for (column = 0u; column < 16u; ++column) {
            g_oled_framebuffer[base + column] = oled_font_completed[character][column];
            g_oled_framebuffer[base + OLED_WIDTH + column] =
                oled_font_completed[character][16u + column];
        }
    }
    oled_refresh_requested = 1u;
}

void OLED_Task(void)
{
    uint8_t position[3];
    uint8_t column;
    static const uint8_t display_on = 0xAFu;
    if (g_oled_present == 0u) { return; }
    if (oled_refresh_active == 0u) {
        if (oled_refresh_requested == 0u) { return; }
        oled_refresh_requested = 0u;
        oled_refresh_active = 1u;
        oled_refresh_offset = 0u;
    }
    column = (uint8_t)(oled_refresh_offset % OLED_WIDTH);
    position[0] = (uint8_t)(0xB0u | (oled_refresh_offset / OLED_WIDTH));
    position[1] = (uint8_t)(column & 0x0Fu);
    position[2] = (uint8_t)(0x10u | (column >> 4));
    /* Each 16-byte span stays within its page (128 is divisible by 16).
     * 0x00 selects command stream; 0x40 selects GDDRAM data stream.
     */
    if (I2C_Transmit(0x00u, position, 3u) == 0u ||
        I2C_Transmit(0x40u, &g_oled_framebuffer[oled_refresh_offset], OLED_CHUNK_BYTES) == 0u) {
        MarkOffline();
        return;
    }
    oled_refresh_offset = (uint16_t)(oled_refresh_offset + OLED_CHUNK_BYTES);
    if (oled_refresh_offset >= OLED_FRAME_BYTES) {
        if (oled_display_on == 0u) {
            if (I2C_Transmit(0x00u, &display_on, 1u) == 0u) { MarkOffline(); return; }
            oled_display_on = 1u;
        }
        oled_refresh_active = 0u;
        ++g_oled_frame_count;
    }
}
