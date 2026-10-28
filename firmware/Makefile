# make                      -> Black Pill (the ROV board)
# make BOARD=bluepill       -> Blue Pill (bench)
# make flash [BOARD=..]     -> via ST-LINK (st-flash)
# make dfu                  -> Black Pill over USB-C: hold BOOT0, tap NRST, release BOOT0, then run this
BOARD ?= blackpill
BAUD  ?= 460800
CC       = arm-none-eabi-gcc
OBJCOPY  = arm-none-eabi-objcopy
SIZE     = arm-none-eabi-size
CPU_blackpill = -mcpu=cortex-m4
CPU_bluepill  = -mcpu=cortex-m3
LD_blackpill  = blackpill/stm32f4.ld
LD_bluepill   = bluepill/stm32f103c8.ld
CFLAGS = $(CPU_$(BOARD)) -mthumb -Os -Wall -Wextra -Werror -ffreestanding -fno-builtin \
         -fno-tree-loop-distribute-patterns -nostdlib -Icommon -DBAUD=$(BAUD)u
OUT = build/$(BOARD)
SRC = common/app.c common/core.c $(BOARD)/board.c

$(OUT)/rovfw.bin: $(OUT)/rovfw.elf
	$(OBJCOPY) -O binary $< $@
	$(SIZE) $<

$(OUT)/rovfw.elf: $(SRC) common/core.h common/board.h $(LD_$(BOARD))
	@test -n "$(CPU_$(BOARD))" || { echo "unknown BOARD=$(BOARD) (blackpill | bluepill)"; exit 1; }
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -T $(LD_$(BOARD)) $(SRC) -o $@

flash: $(OUT)/rovfw.bin
	st-flash --reset write $< 0x08000000

dfu: $(OUT)/rovfw.bin
	dfu-util -a 0 -s 0x08000000:leave -D $<

clean:
	rm -rf build
.PHONY: flash dfu clean
