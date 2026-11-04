/* Black Pill (STM32F401CC / STM32F411CE, WeAct) port. Bare metal, no HAL/CMSIS.
 * Clock: 25 MHz crystal (HSE) -> PLL 84 MHz, valid on F401 and F411. If the crystal doesn't start, falls back to the
 *        internal 16 MHz RC (HSI) -> 84 MHz. APB1 42 MHz (timers 84), APB2 84. FPU on (hard float).
 * UART:  USART1 460800 8N1, both directions by DMA2 (RX: stream 2 circular 1 KB ring, TX: stream 7), no per-byte IRQ.
 * PWM:   TIM3 CH1-4 + TIM4 CH1-4, 1 MHz tick, 20 ms period, preloaded (an update never makes a runt pulse).
 * Flash: sector 5 (0x08020000, 128 KB) holds the parameters; the firmware is limited to 128 KB by the linker script.
 * Pinout: see common/board.h. Flash with an ST-LINK (make flash) or USB DFU (hold BOOT0, tap NRST; make dfu).
 */
#include <stdint.h>
#include "board.h"
#include "core.h"
#include "params.h"

#ifndef BAUD
#define BAUD 460800u
#endif
#define SYSCLK 84000000u
#define REG(a) (*(volatile uint32_t *)(a))

#define RCC_CR        REG(0x40023800)
#define RCC_PLLCFGR   REG(0x40023804)
#define RCC_CFGR      REG(0x40023808)
#define RCC_AHB1ENR   REG(0x40023830)
#define RCC_APB1ENR   REG(0x40023840)
#define RCC_APB2ENR   REG(0x40023844)
#define FLASH_ACR     REG(0x40023C00)
#define FLASH_KEYR    REG(0x40023C04)
#define FLASH_SR      REG(0x40023C0C)
#define FLASH_CR      REG(0x40023C10)
#define GPIO(port, o) REG(0x40020000 + 0x400 * (port) + (o))
#define MODER 0x00
#define PUPDR 0x0C
#define BSRR  0x18
#define AFRL  0x20
#define AFRH  0x24
#define PA 0
#define PB 1
#define PC 2
#define USART1_SR     REG(0x40011000)
#define USART1_DR_ADDR 0x40011004u
#define USART1_BRR    REG(0x40011008)
#define USART1_CR1    REG(0x4001100C)
#define USART1_CR3    REG(0x40011014)
#define DMA2_HIFCR    REG(0x4002640C)
#define DMA2_S(n, o)  REG(0x40026410 + 0x18 * (n) + (o))
#define S_CR 0x00
#define S_NDTR 0x04
#define S_PAR 0x08
#define S_M0AR 0x0C
#define TIM3(o)       REG(0x40000400 + (o))
#define TIM4(o)       REG(0x40000800 + (o))
#define CR1 0x00
#define EGR 0x14
#define CCMR1 0x18
#define CCMR2 0x1C
#define CCER 0x20
#define PSC 0x28
#define ARR 0x2C
#define CCR1 0x34
#define CCR2 0x38
#define CCR3 0x3C
#define CCR4 0x40
#define IWDG_KR       REG(0x40003000)
#define IWDG_PR       REG(0x40003004)
#define IWDG_RLR      REG(0x40003008)
#define IWDG_SR       REG(0x4000300C)
#define SYST_CSR      REG(0xE000E010)
#define SYST_RVR      REG(0xE000E014)
#define SYST_CVR      REG(0xE000E018)
#define SCB_CPACR     REG(0xE000ED88)
#define DEMCR         REG(0xE000EDFC)
#define DWT_CTRL      REG(0xE0001000)
#define DWT_CYCCNT    REG(0xE0001004)

#define PARAM_FLASH   0x08020000u          /* sector 5 */
#define PARAM_WORDS   (128u * 1024u / 4u)

static void af(int port, int pin, int fn)
{
    GPIO(port, MODER) = (GPIO(port, MODER) & ~(3u << (2 * pin))) | (2u << (2 * pin));
    uint32_t off = pin < 8 ? AFRL : AFRH, sh = 4u * (pin & 7);
    GPIO(port, off) = (GPIO(port, off) & ~(0xFu << sh)) | ((uint32_t)fn << sh);
}

void board_outputs(const uint16_t *u)
{
    TIM3(CCR1) = u[0]; TIM3(CCR2) = u[1]; TIM3(CCR3) = u[2]; TIM3(CCR4) = u[3];
    TIM4(CCR1) = u[4]; TIM4(CCR2) = u[5]; TIM4(CCR3) = u[OUT_LIGHTS]; TIM4(CCR4) = u[OUT_GIMBAL];
}

void board_led(int on) { GPIO(PC, BSRR) = on ? (1u << (13 + 16)) : (1u << 13); }

/* ---- UART over DMA ---- */
#define RXN 1024u
static uint8_t  rxring[RXN];
static uint32_t rx_rd;
static uint8_t  txdma[256];

int board_uart_read(uint8_t *dst, int max)
{
    uint32_t wr = RXN - DMA2_S(2, S_NDTR);              /* where the DMA will write next */
    if (wr >= RXN) wr = 0;
    int n = 0;
    while (rx_rd != wr && n < max) { dst[n++] = rxring[rx_rd]; rx_rd = (rx_rd + 1) % RXN; }
    return n;
}

int board_uart_write(const uint8_t *p, int n)
{
    if (DMA2_S(7, S_CR) & 1u) return 0;                 /* previous transfer still running */
    if (n > (int)sizeof txdma) n = sizeof txdma;
    if (n <= 0) return 0;
    for (int i = 0; i < n; i++) txdma[i] = p[i];
    DMA2_HIFCR = 0x0F400000u;                            /* clear stream 7 flags */
    DMA2_S(7, S_M0AR) = (uint32_t)txdma;
    DMA2_S(7, S_NDTR) = (uint32_t)n;
    DMA2_S(7, S_CR) |= 1u;
    return n;
}

void board_kick_watchdog(void) { IWDG_KR = 0xAAAA; }
uint32_t board_cycles(void) { return DWT_CYCCNT; }
uint32_t board_cycles_per_us(void) { return SYSCLK / 1000000u; }
void board_idle(void) { __asm volatile ("wfi"); }

/* ---- parameter flash ---- */
static void iwdg_timeout(uint32_t pr, uint32_t rlr)
{
    while (IWDG_SR & 3u) { }
    IWDG_KR = 0x5555; IWDG_PR = pr; IWDG_RLR = rlr;
    while (IWDG_SR & 3u) { }
    IWDG_KR = 0xAAAA;
}

static int flash_wait(void)
{
    while (FLASH_SR & (1u << 16)) { }
    uint32_t err = FLASH_SR & 0xF2u;                     /* PGSERR PGPERR PGAERR WRPERR OPERR */
    FLASH_SR = 0xF3u;
    return err ? 1 : 0;
}

static void flash_unlock(void)
{
    if (FLASH_CR & (1u << 31)) { FLASH_KEYR = 0x45670123u; FLASH_KEYR = 0xCDEF89ABu; }
}

const volatile uint32_t *board_flash_base(void) { return (const volatile uint32_t *)PARAM_FLASH; }
uint32_t board_flash_words(void) { return PARAM_WORDS; }

int board_flash_erase(void)
{
    /* a 128 KB erase stalls the CPU for ~1-2 s (code runs from the same flash): stretch the watchdog to 8 s.
     * The timers keep generating PWM on their own, and the core only allows this while disarmed. */
    iwdg_timeout(4, 4095);
    flash_unlock();
    flash_wait();
    FLASH_CR = (2u << 8) | (5u << 3) | (1u << 1);        /* PSIZE x32, sector 5, SER */
    FLASH_CR |= 1u << 16;                                /* STRT */
    int e = flash_wait();
    FLASH_CR = 1u << 31;                                 /* lock */
    FLASH_ACR &= ~(1u << 10);                            /* data cache may hold the old contents: flush */
    FLASH_ACR |= 1u << 12;
    FLASH_ACR &= ~(1u << 12);
    FLASH_ACR |= 1u << 10;
    iwdg_timeout(2, 500);
    return e;
}

int board_flash_write(uint32_t off, const uint32_t *w, uint32_t n)
{
    if (off + n > PARAM_WORDS) return 1;
    flash_unlock();
    flash_wait();
    int e = 0;
    FLASH_CR = (2u << 8) | 1u;                           /* PSIZE x32, PG */
    for (uint32_t i = 0; i < n && !e; i++) {
        REG(PARAM_FLASH + 4u * (off + i)) = w[i];
        e = flash_wait();
    }
    FLASH_CR = 1u << 31;
    return e;
}

void board_init(void)
{
    /* 84 MHz: PLL input must be 1 MHz -> M = 25 (HSE) or 16 (HSI); then x336 /4 = 84, /7 = 48 */
    FLASH_ACR = (1u << 10) | (1u << 9) | (1u << 8) | 2u;                 /* D/I cache, prefetch, 2 WS */
    RCC_CR |= 1u << 16;                                                  /* HSE on */
    uint32_t n = 0;
    while (!(RCC_CR & (1u << 17)) && ++n < 500000u) { }                  /* ~100 ms max, typ. 2 ms */
    if (RCC_CR & (1u << 17))
        RCC_PLLCFGR = (1u << 29) | (7u << 24) | (1u << 22) | (1u << 16) | (336u << 6) | 25u;
    else {
        RCC_CR &= ~(1u << 16);
        RCC_PLLCFGR = (1u << 29) | (7u << 24) | (1u << 16) | (336u << 6) | 16u;
    }
    RCC_CFGR = 4u << 10;                                                 /* APB1 = /2 */
    RCC_CR |= 1u << 24;
    while (!(RCC_CR & (1u << 25))) { }
    RCC_CFGR |= 2u;
    while (((RCC_CFGR >> 2) & 3u) != 2u) { }

    RCC_AHB1ENR |= 7u | (1u << 22);                                      /* GPIOA B C, DMA2 */
    RCC_APB1ENR |= (1u << 1) | (1u << 2);                                /* TIM3 TIM4 */
    RCC_APB2ENR |= 1u << 4;                                              /* USART1 */

    DEMCR |= 1u << 24; DWT_CYCCNT = 0; DWT_CTRL |= 1u;                   /* cycle counter for loop timing */

    GPIO(PC, MODER) = (GPIO(PC, MODER) & ~(3u << 26)) | (1u << 26);      /* PC13 output */
    board_led(0);

    /* PWM: 1 MHz tick, 20 ms period. Neutral pulses are loaded BEFORE the pins are switched to the timer. */
    static const uint16_t neutral[N_OUTPUTS] = { 1500, 1500, 1500, 1500, 1500, 1500, LIGHTS_OFF_US, 1500 };
    TIM3(PSC) = 83; TIM3(ARR) = 19999;
    TIM4(PSC) = 83; TIM4(ARR) = 19999;
    board_outputs(neutral);
    TIM3(CCMR1) = 0x6868; TIM3(CCMR2) = 0x6868;                          /* PWM mode 1, preload */
    TIM4(CCMR1) = 0x6868; TIM4(CCMR2) = 0x6868;
    TIM3(CCER) = 0x1111;  TIM4(CCER) = 0x1111;
    TIM3(EGR) = 1; TIM4(EGR) = 1;
    TIM3(CR1) = (1u << 7) | 1u; TIM4(CR1) = (1u << 7) | 1u;
    af(PA, 6, 2); af(PA, 7, 2); af(PB, 0, 2); af(PB, 1, 2);
    af(PB, 6, 2); af(PB, 7, 2); af(PB, 8, 2); af(PB, 9, 2);

    /* USART1 on PA9/PA10 (AF7), pull-up on RX so an unplugged wire reads idle instead of noise */
    af(PA, 9, 7); af(PA, 10, 7);
    GPIO(PA, PUPDR) = (GPIO(PA, PUPDR) & ~(3u << 20)) | (1u << 20);
    USART1_BRR = (SYSCLK + BAUD / 2) / BAUD;
    USART1_CR3 = (1u << 6) | (1u << 7);                                  /* DMAR DMAT */
    /* RX: DMA2 stream 2 channel 4, circular, byte, memory increment, high priority */
    DMA2_S(2, S_PAR) = USART1_DR_ADDR; DMA2_S(2, S_M0AR) = (uint32_t)rxring; DMA2_S(2, S_NDTR) = RXN;
    DMA2_S(2, S_CR) = (4u << 25) | (2u << 16) | (1u << 10) | (1u << 8) | 1u;
    /* TX: DMA2 stream 7 channel 4, memory -> peripheral, enabled per transfer by board_uart_write() */
    DMA2_S(7, S_PAR) = USART1_DR_ADDR;
    DMA2_S(7, S_CR) = (4u << 25) | (2u << 16) | (1u << 10) | (1u << 6);
    USART1_CR1 = (1u << 13) | (1u << 3) | (1u << 2);                     /* UE TE RE */

    SYST_RVR = SYSCLK / 1000u - 1u; SYST_CVR = 0; SYST_CSR = 7;
    IWDG_KR = 0xCCCC; IWDG_KR = 0x5555; IWDG_PR = 2; IWDG_RLR = 500;     /* 32 kHz/16 -> 250 ms */
    IWDG_KR = 0xAAAA;
}

void SysTick_Handler(void) { app_tick_1ms(); }

/* ---- startup ---- */
extern uint32_t _estack, _sidata, _sdata, _edata, _sbss, _ebss;
int main(void);

void Reset_Handler(void)
{
    SCB_CPACR |= 0xFu << 20;                             /* FPU on before any float instruction */
    __asm volatile ("dsb\n isb");
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;
    for (dst = &_sbss; dst < &_ebss; ) *dst++ = 0;
    main();
    for (;;) { }
}

/* Any fault: every thruster to neutral, then the watchdog resets the chip. */
void Fault_Handler(void)
{
    TIM3(CCR1) = TIM3(CCR2) = TIM3(CCR3) = TIM3(CCR4) = 1500;
    TIM4(CCR1) = TIM4(CCR2) = 1500;
    for (;;) { }
}

__attribute__((section(".isr_vector"), used))
void (* const vectors[16 + 86])(void) = {
    [0] = (void (*)(void))&_estack,
    [1] = Reset_Handler,
    [2] = Fault_Handler, [3] = Fault_Handler, [4] = Fault_Handler, [5] = Fault_Handler, [6] = Fault_Handler,
    [15] = SysTick_Handler,
};
