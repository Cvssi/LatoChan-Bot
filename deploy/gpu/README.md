# Optional GPU tuning (AMD Vega, hardware specific)

`gpu-tune`, `gpu-fan` and `gpu-watchdog` (+ their `.service` units) are examples from a Radeon Vega 56 home server: undervolt and power limit at boot, a fan curve on hotspot temperature, and a watchdog that reboots if the GPU falls off the PCIe bus.

**They are not generic.** The PCI address (`0000:05:00.0`), clocks, voltages and fan curve are specific to one card. Do not install them unchanged on other hardware. If you use them, copy the scripts to `/usr/local/sbin`, the units to `/etc/systemd/system`, then `systemctl enable --now gpu-tune gpu-fan gpu-watchdog`. `../systemd/ollama.service.d/after-gpu-tune.conf` orders Ollama after the tuning service.
