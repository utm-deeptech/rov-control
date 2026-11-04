# make                      -> Black Pill (the ROV board)
# make SIM=1                -> + bench plant model: fake depth/IMU so the PID holds can be tried with no sensors
# make BOARD=bluepill       -> Blue Pill (bench)
# make flash [BOARD=..]     -> via ST-LINK (openocd)
# make dfu                  -> Black Pill over USB-C: hold BOOT0, tap NRST, release BOOT0, then run this
BOARD ?= blackpill
BAUD  ?= 460800
SIM   ?= 0
CC       = arm-none-eabi-gcc
OBJCOPY  = arm-none-eabi-objcopy
SIZE     = arm-none-eabi-size
CPU_blackpill = -mcpu=cortex-m4 -mfloat-abi=hard -mfpu=fpv4-sp-d16
CPU_bluepill  = -mcpu=cortex-m3 -mfloat-abi=soft
LD_blackpill  = blackpill/stm32f4.ld
LD_bluepill   = bluepill/stm32f103c8.ld
OCD_blackpill = target/stm32f4x.cfg
OCD_bluepill  = target/stm32f1x.cfg
CFLAGS = $(CPU_$(BOARD)) -mthumb -Os -Wall -Wextra -Werror -ffreestanding -fno-builtin \
         -fno-tree-loop-distribute-patterns -nostdlib -Icommon -DBAUD=$(BAUD)u -fsingle-precision-constant \
         -Wdouble-promotion
CORE = common/core.c common/proto.c common/params.c common/pid.c common/mixer.c
SRC  = common/app.c common/libc_min.c $(CORE) $(BOARD)/board.c
ifeq ($(SIM),1)
CFLAGS += -DBENCH_SIM
SRC    += common/sim.c
OUT = build/$(BOARD)-sim
else
OUT = build/$(BOARD)
endif

$(OUT)/rovfw.bin: $(OUT)/rovfw.elf
	$(OBJCOPY) -O binary $< $@
	$(SIZE) $<

$(OUT)/rovfw.elf: $(SRC) $(wildcard common/*.h) $(LD_$(BOARD)) Makefile
	@test -n "$(CPU_$(BOARD))" || { echo "unknown BOARD=$(BOARD) (blackpill | bluepill)"; exit 1; }
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -T $(LD_$(BOARD)) $(SRC) -lgcc -o $@

flash: $(OUT)/rovfw.bin
	openocd -f interface/stlink.cfg -f $(OCD_$(BOARD)) -c "program $(OUT)/rovfw.elf verify reset exit"

dfu: $(OUT)/rovfw.bin
	dfu-util -a 0 -s 0x08000000:leave -D $<

clean:
	rm -rf build
.PHONY: flash dfu clean
