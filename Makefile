# Same behaviour on every laptop: ignore ~/.local packages and ROS's PYTHONPATH
export PYTHONNOUSERSITE := 1
unexport PYTHONPATH
export PYTEST_DISABLE_PLUGIN_AUTOLOAD := 1

PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: fw fw-flash fw-dfu pi-swd pi-fake setup run run-sim test deploy-pi deploy-pi-full flash reset gamepad-test network ssh desktop

setup:            ## laptop packages + venv (Phase 1)
	bash scripts/setup_laptop.sh

test:             ## protocol, mixer and end-to-end sim tests
	$(PY) -m pytest -q protocol topside sim firmware

run-sim:          ## everything on the laptop, no hardware
	bash sim/run_sim.sh

run:              ## the app (Link menu: TETHER = FXTI, WI-FI = bench)
	$(PY) topside/app.py

fw:               ## build Black Pill firmware (BOARD=bluepill for the bench board)
	$(MAKE) -C firmware BOARD=$(or $(BOARD),blackpill)

fw-flash:         ## flash over an ST-LINK on THIS laptop
	$(MAKE) -C firmware BOARD=$(or $(BOARD),blackpill) flash

fw-dfu:           ## flash Black Pill over USB-C (hold BOOT0, tap NRST, release BOOT0 first)
	$(MAKE) -C firmware dfu

network:          ## static 192.168.2.1/24 on the FXTI interface
	bash scripts/setup_network.sh

ssh:              ## passwordless ssh + 'rovpi' alias
	bash scripts/setup_ssh.sh

deploy-pi:        ## push code + restart services (no apt)
	bash scripts/deploy_pi.sh --no-apt

deploy-pi-full:   ## first deploy: also apt-installs packages (Pi needs internet)
	bash scripts/deploy_pi.sh

flash:            ## make flash BIN=firmware.bin
	bash scripts/flash.sh $(BIN)

reset:
	bash scripts/reset.sh

gamepad-test:     ## show raw axis/button numbers
	$(PY) scripts/gamepad_test.py

desktop:          ## install the app launcher in your applications menu
	mkdir -p ~/.local/share/applications
	sed "s|@ROOT@|$(CURDIR)|g" rov-control.desktop > ~/.local/share/applications/rov-control.desktop
	@echo "Installed: search 'ROV Control' in the app menu"

pi-fake:          ## fake Blackpill on the Pi (test over the real tether, no firmware)
	ssh -t $$($(PY) config/cfg.py pi.ssh_alias) "bash ~/$$($(PY) config/cfg.py pi.install_dir)/sim/pi_fake.sh"

pi-swd:           ## bench: drive the board through the ST-LINK on the Pi (no UART wires needed)
	ssh -t $$($(PY) config/cfg.py pi.ssh_alias) "cd ~/$$($(PY) config/cfg.py pi.install_dir) && python3 pi/swd_bridge.py"
