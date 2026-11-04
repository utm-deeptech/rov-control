/* Host (laptop) stand-in for the board's parameter flash, so params.c is tested for real. */
#include <stdint.h>
#include <string.h>

#define WORDS 2048u
static uint32_t mem[WORDS];
static int erased_once;
int flash_emu_erases;

static void wipe(void) { memset(mem, 0xFF, sizeof mem); }
void flash_emu_wipe(void) { wipe(); erased_once = 1; flash_emu_erases = 0; }

const volatile uint32_t *board_flash_base(void) { if (!erased_once) flash_emu_wipe(); return mem; }
uint32_t board_flash_words(void) { return WORDS; }
int board_flash_erase(void) { wipe(); flash_emu_erases++; return 0; }
int board_flash_write(uint32_t off, const uint32_t *w, uint32_t n)
{
    if (off + n > WORDS) return 1;
    for (uint32_t i = 0; i < n; i++) {
        if (mem[off + i] != 0xFFFFFFFFu) return 1;     /* like real flash: only erased words can be written */
        mem[off + i] = w[i];
    }
    return 0;
}
/* simulate a power cut half-way through a save: corrupt one word of the last record */
void flash_emu_poke(uint32_t off, uint32_t v) { mem[off] = v; }
