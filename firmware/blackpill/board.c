/* Black Pill (STM32F401CC / STM32F411CE, WeAct) port. Bare metal, no HAL/CMSIS.
 * Clock: HSI 16 MHz -> PLL 84 MHz (valid on F401 and F411, no crystal dependency). APB1 42 MHz (timers 84), APB2 84.
 * Pinout: see common/board.h. Flash: ST-LINK (make flash) or USB DFU (hold BOOT0, tap NRST; make dfu).
 */
#include <stdint.h>
#include "board.h"

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
#define USART1_DR     REG(0x40011004)
#define USART1_BRR    REG(0x40011008)
#define USART1_CR1    REG(0x4001100C)
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
#define NVIC_ISER1    REG(0xE000E104)
#define SYST_CSR      REG(0xE000E010)
#define SYST_RVR      REG(0xE000E014)
#define SYST_CVR      REG(0xE000E018)

static void af(int port, int pin, int fn)
{
    GPIO(port, MODER) = (GPIO(port, MODER) & ~(3u << (2 * pin))) | (2u << (2 * pin));
    uint32_t off = pin < 8 ? AFRL : AFRH, sh = 4u * (pin & 7);
    GPIO(port, off) = (GPIO(port, off) & ~(0xFu << sh)) | ((uint32_t)fn << sh);
}

void board_outputs(const uint16_t *t, uint16_t lights)
{
    TIM3(CCR1) = t[0]; TIM3(CCR2) = t[1]; TIM3(CCR3) = t[2]; TIM3(CCR4) = t[3];
    TIM4(CCR1) = t[4]; TIM4(CCR2) = t[5]; TIM4(CCR3) = lights;
}

void board_led(int on) { GPIO(PC, BSRR) = on ? (1u << (13 + 16)) : (1u << 13); }

void board_uart_write(const uint8_t *p, int n)
{
    while (n--) {
        while (!(USART1_SR & (1u << 7))) { }
        USART1_DR = *p++;
    }
}

void board_kick_watchdog(void) { IWDG_KR = 0xAAAA; }

void board_init(void)
{
    /* 84 MHz from HSI */
    FLASH_ACR = (1u << 10) | (1u << 9) | (1u << 8) | 2u;                 /* D/I cache, prefetch, 2 WS */
    RCC_PLLCFGR = (1u << 29) | (7u << 24) | (1u << 16) | (336u << 6) | 16u; /* HSI/16*336/4 = 84, Q = 48 MHz */
    RCC_CFGR = 4u << 10;                                                 /* APB1 = /2 */
    RCC_CR |= 1u << 24;
    while (!(RCC_CR & (1u << 25))) { }
    RCC_CFGR |= 2u;
    while (((RCC_CFGR >> 2) & 3u) != 2u) { }

    RCC_AHB1ENR |= 7u;                                                   /* GPIOA B C */
    RCC_APB1ENR |= (1u << 1) | (1u << 2);                                /* TIM3 TIM4 */
    RCC_APB2ENR |= 1u << 4;                                              /* USART1 */

    GPIO(PC, MODER) = (GPIO(PC, MODER) & ~(3u << 26)) | (1u << 26);      /* PC13 output */
    board_led(0);

    /* PWM: 1 MHz tick, 20 ms period. Neutral pulses are loaded BEFORE the pins are switched to the timer. */
    static const uint16_t neutral[6] = { 1500, 1500, 1500, 1500, 1500, 1500 };
    TIM3(PSC) = 83; TIM3(ARR) = 19999;
    TIM4(PSC) = 83; TIM4(ARR) = 19999;
    board_outputs(neutral, 1100);
    TIM3(CCMR1) = 0x6868; TIM3(CCMR2) = 0x6868;
    TIM4(CCMR1) = 0x6868; TIM4(CCMR2) = 0x0068;
    TIM3(CCER) = 0x1111;  TIM4(CCER) = 0x0111;
    TIM3(EGR) = 1; TIM4(EGR) = 1;
    TIM3(CR1) = (1u << 7) | 1u; TIM4(CR1) = (1u << 7) | 1u;
    af(PA, 6, 2); af(PA, 7, 2); af(PB, 0, 2); af(PB, 1, 2);
    af(PB, 6, 2); af(PB, 7, 2); af(PB, 8, 2);

    /* USART1 on PA9/PA10 (AF7), pull-up on RX so an unplugged wire reads idle instead of noise */
    af(PA, 9, 7); af(PA, 10, 7);
    GPIO(PA, PUPDR) = (GPIO(PA, PUPDR) & ~(3u << 20)) | (1u << 20);
    USART1_BRR = (SYSCLK + BAUD / 2) / BAUD;
    USART1_CR1 = (1u << 13) | (1u << 5) | (1u << 3) | (1u << 2);         /* UE RXNEIE TE RE */
    NVIC_ISER1 = 1u << 5;                                                /* IRQ37 = USART1 */

    SYST_RVR = SYSCLK / 1000u - 1u; SYST_CVR = 0; SYST_CSR = 7;
    IWDG_KR = 0xCCCC; IWDG_KR = 0x5555; IWDG_PR = 2; IWDG_RLR = 500;     /* 32 kHz/16 -> 250 ms */
    IWDG_KR = 0xAAAA;
}

void SysTick_Handler(void) { app_tick_1ms(); }

void USART1_IRQHandler(void)
{
    uint32_t sr = USART1_SR;
    if (sr & ((1u << 5) | (1u << 3))) app_rx_byte((uint8_t)USART1_DR);  /* RXNE / ORE: DR read clears */
}

/* ---- startup ---- */
extern uint32_t _estack, _sidata, _sdata, _edata, _sbss, _ebss;
int main(void);

void Reset_Handler(void)
{
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
    [16 + 37] = USART1_IRQHandler,
};
