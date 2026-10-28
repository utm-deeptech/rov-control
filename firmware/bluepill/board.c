/* Blue Pill (STM32F103C8) port — bench board only; the ROV uses the Black Pill (blackpill/).
 * Clock: HSI/2 x16 = 64 MHz (no crystal dependency). APB1 32 MHz (timers 64), APB2 64. Pinout: common/board.h.
 */
#include <stdint.h>
#include "board.h"

#ifndef BAUD
#define BAUD 460800u
#endif
#define SYSCLK 64000000u

#define REG(a) (*(volatile uint32_t *)(a))
#define RCC_CR        REG(0x40021000)
#define RCC_CFGR      REG(0x40021004)
#define RCC_APB2ENR   REG(0x40021018)
#define RCC_APB1ENR   REG(0x4002101C)
#define FLASH_ACR     REG(0x40022000)
#define GPIOA_CRL     REG(0x40010800)
#define GPIOA_CRH     REG(0x40010804)
#define GPIOB_CRL     REG(0x40010C00)
#define GPIOC_CRH     REG(0x40011004)
#define GPIOC_BSRR    REG(0x40011010)
#define USART1_SR     REG(0x40013800)
#define USART1_DR     REG(0x40013804)
#define USART1_BRR    REG(0x40013808)
#define USART1_CR1    REG(0x4001380C)
#define TIM3(o)       REG(0x40000400 + (o))
#define TIM4(o)       REG(0x40000800 + (o))
#define IWDG_KR       REG(0x40003000)
#define IWDG_PR       REG(0x40003004)
#define IWDG_RLR      REG(0x40003008)
#define NVIC_ISER1    REG(0xE000E104)
#define SYST_CSR      REG(0xE000E010)
#define SYST_RVR      REG(0xE000E014)
#define SYST_CVR      REG(0xE000E018)

/* timer register offsets */
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

#define GPIOB_CRH     REG(0x40010C04)

void board_outputs(const uint16_t *t, uint16_t lights)
{
    TIM3(CCR1) = t[0]; TIM3(CCR2) = t[1]; TIM3(CCR3) = t[2]; TIM3(CCR4) = t[3];
    TIM4(CCR1) = t[4]; TIM4(CCR2) = t[5]; TIM4(CCR3) = lights;
}

void board_led(int on) { GPIOC_BSRR = on ? (1u << (13 + 16)) : (1u << 13); }

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
    FLASH_ACR = 0x12;                              /* prefetch, 2 wait states */
    RCC_CFGR = (14u << 18) | (4u << 8);            /* PLL x16 from HSI/2; APB1 = /2 */
    RCC_CR |= (1u << 24);
    while (!(RCC_CR & (1u << 25))) { }
    RCC_CFGR |= 2u;
    while (((RCC_CFGR >> 2) & 3u) != 2u) { }

    RCC_APB2ENR |= (1u << 0) | (1u << 2) | (1u << 3) | (1u << 4) | (1u << 14);   /* AFIO A B C USART1 */
    RCC_APB1ENR |= (1u << 1) | (1u << 2);                                          /* TIM3 TIM4 */
    GPIOC_CRH = (GPIOC_CRH & ~(0xFu << 20)) | (0x2u << 20);
    board_led(0);

    static const uint16_t neutral[6] = { 1500, 1500, 1500, 1500, 1500, 1500 };
    TIM3(PSC) = 63; TIM3(ARR) = 19999;
    TIM4(PSC) = 63; TIM4(ARR) = 19999;
    board_outputs(neutral, 1100);
    TIM3(CCMR1) = 0x6868; TIM3(CCMR2) = 0x6868;
    TIM4(CCMR1) = 0x6868; TIM4(CCMR2) = 0x0068;
    TIM3(CCER) = 0x1111;  TIM4(CCER) = 0x0111;
    TIM3(EGR) = 1; TIM4(EGR) = 1;
    TIM3(CR1) = (1u << 7) | 1u; TIM4(CR1) = (1u << 7) | 1u;

    /* PA6/PA7, PB0/PB1, PB6/PB7, PB8: AF push-pull 2 MHz. PA9 TX AF-PP, PA10 RX input with pull-up. */
    GPIOA_CRL = (GPIOA_CRL & ~(0xFFu << 24)) | (0xAAu << 24);
    GPIOA_CRH = (GPIOA_CRH & ~((0xFu << 4) | (0xFu << 8))) | (0xBu << 4) | (0x8u << 8);
    REG(0x40010810) = 1u << 10;                                                    /* GPIOA_BSRR: PA10 pull-up */
    GPIOB_CRL = (GPIOB_CRL & ~((0xFFu << 0) | (0xFFu << 24))) | (0xAAu << 0) | (0xAAu << 24);
    GPIOB_CRH = (GPIOB_CRH & ~0xFu) | 0xAu;

    USART1_BRR = (SYSCLK + BAUD / 2) / BAUD;
    USART1_CR1 = (1u << 13) | (1u << 5) | (1u << 3) | (1u << 2);
    NVIC_ISER1 = 1u << 5;

    SYST_RVR = SYSCLK / 1000u - 1u; SYST_CVR = 0; SYST_CSR = 7;
    IWDG_KR = 0xCCCC; IWDG_KR = 0x5555; IWDG_PR = 2; IWDG_RLR = 625;              /* 40 kHz/16 -> 250 ms */
    IWDG_KR = 0xAAAA;
}

void SysTick_Handler(void) { app_tick_1ms(); }

void USART1_IRQHandler(void)
{
    uint32_t sr = USART1_SR;
    if (sr & ((1u << 5) | (1u << 3))) app_rx_byte((uint8_t)USART1_DR);
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

/* Any fault: park every thruster at neutral, then let the watchdog reset the chip. */
void Fault_Handler(void)
{
    TIM3(CCR1) = TIM3(CCR2) = TIM3(CCR3) = TIM3(CCR4) = 1500;
    TIM4(CCR1) = TIM4(CCR2) = 1500;
    for (;;) { }
}

__attribute__((section(".isr_vector"), used))
void (* const vectors[59])(void) = {
    [0] = (void (*)(void))&_estack,
    [1] = Reset_Handler,
    [2] = Fault_Handler,            /* NMI */
    [3] = Fault_Handler,            /* HardFault */
    [4] = Fault_Handler,            /* MemManage */
    [5] = Fault_Handler,            /* BusFault */
    [6] = Fault_Handler,            /* UsageFault */
    [15] = SysTick_Handler,
    [16 + 37] = USART1_IRQHandler,
};
