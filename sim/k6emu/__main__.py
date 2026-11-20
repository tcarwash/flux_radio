"""Boot a K6 firmware binary on a Cortex-M0 and report virtual-board activity.

This first board slice is intentionally diagnostic: it models boot clocks, basic GPIO,
USART, SPI FIFO readiness, and free-running timers. Unmodelled details are visible in the
MMIO trace instead of silently claiming hardware fidelity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter, deque
from pathlib import Path
from .lcd import SC5260
from .rx_dma import DMA_BASE, GpioRxDma, MEASURED_FREE_RUNNING_HZ
from .if_resample import held_gpio_samples


FLASH_BASE = 0x08000000
FLASH_BYTES = 128 * 1024
APP_BASE = 0x08002000
RAM_BASE = 0x20000000
RAM_BYTES = 24 * 1024
APP_RAM_BASE = 0x20001028

# KD32F328CB addresses from the PAC SVD checked into the Aura source tree.
MMIO_RANGES = ((0x40000000, 0x40025000), (0x48000000, 0x48002000), (0xE000E000, 0xE000F000))
RCC = 0x40021000
GPIO_BASES = {0x48000000: "A", 0x48000400: "B", 0x48000800: "C",
              0x48000C00: "D", 0x48001400: "F"}
USART1 = 0x40013800
USART1_VECTOR_INDEX = 43              # KD32F328 vector 16 + USART1 IRQ 27
USART1_IRQ_BIT = 1 << 27
SPI1 = 0x40013000
SPI2 = 0x40003800
TIM14 = 0x40002000
TIM6 = 0x40001000
TIM17 = 0x40014800
DMA = 0x40020000
ADC = 0x40012400
SYSCFG_CFGR1 = 0x40010000
FLASH_ACR = 0x40022000
NVIC_ISER = 0xE000E100
NVIC_ICER = 0xE000E180
NVIC_ISPR = 0xE000E200
NVIC_ICPR = 0xE000E280
NVIC_IABR = 0xE000E300
NVIC_IPR = 0xE000E400


def _unicorn():
    try:
        import unicorn
        from unicorn import arm_const
        return unicorn, arm_const
    except ImportError as exc:
        raise SystemExit("Install the emulator dependency with: python3 -m pip install -r "
                         "sim/k6emu/requirements.txt") from exc


def overlay_module_bundle(flash_data: bytearray, bundle_path: Path, key_path: Path | None,
                          selected_slots: set[int] | None = None, *, serial_start: int | None = None):
    """Sign bundle modules and install them into the virtual SPI library slots."""
    # Reuse the release installer so signatures and module headers match what the radio accepts.
    tool_dir = str(Path(__file__).resolve().parents[2] / "tools")
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import k6_install
    from modem import k6mod

    from k6_user_store_migrate import BASE,END,is_packed,objects,compact
    raw=flash_data[BASE:END];packed=is_packed(raw);catalog=objects(raw) if packed else None
    bundle = k6_install.load_bundle(str(bundle_path))
    key = k6mod.read_key(str(key_path)) if key_path is not None else None
    serials = k6_install.Serials()
    entries = [dict(entry) for entry in bundle["library"] if entry["kind"] == "module"]
    fixed_slots = {entry["slot"] for entry in entries if entry.get("slot") is not None}
    used_auto_slots = set()
    for entry in entries:
        if entry.get("slot") is not None:
            continue
        module_id = entry.get("id")
        if not module_id:
            raise ValueError(f"{entry.get('name', 'module')}: automatic bundle entry has no stable id")
        marker = k6_install.auto_marker(module_id)
        if Path(entry["path"]).stat().st_size + len(marker) + 24 > k6_install.LIB_SLOT:
            raise ValueError(f"{entry.get('name', module_id)} does not fit in a module slot")
        matches, free = [], []
        for candidate in k6_install.AUTO_SLOTS:
            if candidate in fixed_slots or candidate in used_auto_slots:
                continue
            address = k6_install.LIB_BASE + candidate * k6_install.LIB_SLOT
            existing=catalog.get(candidate,b'') if packed else bytes(flash_data[address:address+k6_install.LIB_SLOT])
            header=existing[:16] if existing else b'\xff'*16
            if header == b"\xff" * 16:
                if (packed and not existing) or (not packed and existing == b"\xff" * k6_install.LIB_SLOT):
                    free.append(candidate)
                continue
            if header[:4] != b"K6M" + bytes([k6mod.ABI]) or header[13] != k6mod.LIB_FLAG:
                continue
            length = int.from_bytes(header[4:6], "little")
            total = 16 + length + 8
            image_at = existing[:total]
            if (length >= 24 and total <= k6_install.LIB_SLOT
                    and len(image_at) == total
                    and image_at[-8:] == k6mod.image_tag(key, image_at[:-8])
                    and image_at[-32:-8] == marker):
                matches.append(candidate)
        if len(matches) > 1:
            raise ValueError(f"{module_id}: matching module identity appears in multiple slots")
        if matches:
            entry["slot"] = matches[0]
        elif free:
            entry["slot"] = free[0]
        else:
            raise ValueError(f"no free automatic module slots for {entry.get('name', module_id)}")
        entry["_auto"] = True
        used_auto_slots.add(entry["slot"])

    if selected_slots is not None:
        entries = [entry for entry in entries
                   if entry.get("slot") in selected_slots and not entry.get("_auto")]
        found = {entry["slot"] for entry in entries}
        missing = selected_slots - found
        if missing:
            raise ValueError(f"bundle has no fixed module in slot(s): {', '.join(map(str, sorted(missing)))}")
    if not entries:
        raise ValueError("bundle contains no selected module entries")
    installed = []

    for entry in entries:
        slot = entry["slot"]
        if not 0 <= slot < k6_install.LIB_SLOTS:
            raise ValueError(f"module slot out of range: {slot}")
        serial = serials.next() if serial_start is None else serial_start + slot
        image = k6_install.sign(entry, serial, key)
        if len(image) > k6_install.LIB_SLOT:
            raise ValueError(f"{entry['name']} image does not fit slot {slot}: {len(image)} bytes")
        if packed:
            old=catalog.get(slot)
            if old is not None and (old[:3]!=b'K6M' or old[13]!=1):raise ValueError(f'slot {slot} contains protected data')
            catalog[slot]=image;installed.append((slot,entry["name"],len(image)));continue
        address = k6_install.LIB_BASE + slot * k6_install.LIB_SLOT
        end = address + len(image)
        if end > len(flash_data):
            raise ValueError(f"slot {slot} lies outside the virtual SPI flash")
        erase_end = min(address + ((len(image) + 4095) & ~4095), len(flash_data))
        flash_data[address:erase_end] = b"\xff" * (erase_end - address)
        # SPI NOR programming only clears bits; the preceding virtual erase makes this exact.
        for offset, byte in enumerate(image):
            flash_data[address + offset] &= byte
        installed.append((slot, entry["name"], len(image)))
    if packed:flash_data[BASE:END]=compact(catalog)[0]
    return installed


class K6Board:
    """Minimal virtual K6 board around Unicorn's Cortex-M0 instruction engine."""

    def __init__(self, image: Path, *, trace_mmio: bool = False, spi_image: Path | None = None,
                 keys: tuple[str, ...] = (), key_events: tuple[tuple[float, str, bool], ...] = (),
                 gpio_events: tuple[tuple[float, str, int, int], ...] = (),
                 adc_inputs: dict[int, int] | None = None, cpi: float = 1.5,
                 collect_mmio: bool = True):
        unicorn, arm = _unicorn()
        self.ucmod, self.arm = unicorn, arm
        self.trace_mmio = trace_mmio
        # Interactive UI runs do not consume the per-access diagnostic histories.
        # Keep collection enabled by default for CLI users and debugging reports.
        self.collect_mmio = collect_mmio or trace_mmio
        self.regs: dict[int, int] = {}
        # The bootloader leaves MEM_MODE=2 on this K6. Aura's txdog temporarily remaps SRAM
        # to address zero (MEM_MODE=3) to install its HardFault/TIM14 vectors.
        self.regs[SYSCFG_CFGR1] = 2
        self.memory_alias_mapped = False
        self.gpio_odr = {name: 0 for name in GPIO_BASES.values()}
        self.gpio_odr["A"] = 1 << 2      # NOR flash deselected at reset
        self.gpio_odr["B"] = 1 << 12     # LCD deselected
        self.gpio_external: dict[tuple[str, int], int] = {}
        self.gpio_external[("A", 15)] = 0  # radio power switch is on
        self.powered_off = False
        self.uart_rx: deque[int] = deque()
        self.uart_tx = bytearray()
        self.uart_irq_handler = None
        self.spi_tx: dict[int, bytearray] = {SPI1: bytearray(), SPI2: bytearray()}
        self.spi_rx: dict[int, deque[int]] = {SPI1: deque(), SPI2: deque()}
        self.lcd_trace: list[tuple[float, int, int, int]] = []
        self.screen = SC5260()
        self.flash_data = bytearray(b"\xff" * (2 * 1024 * 1024))
        if spi_image:
            dump = spi_image.read_bytes()
            if len(dump) != len(self.flash_data):
                raise ValueError(f"SPI dump must be 2 MiB, got {len(dump)} bytes")
            self.flash_data[:] = dump
        self.flash_wel = False
        self.flash_command = None
        self.flash_address_bytes = bytearray()
        self.flash_address = 0
        self.flash_output_index = 0
        self.flash_program_data = bytearray()
        self.key_matrix = set()
        self.key_scan_epoch = 0
        self.key_sample_epoch = 0
        self.key_sampled_scan = -1
        self.last_key_probe = -1
        self.fd_regs = {0x00: 0x0000, 0x67: 0x0000, 0x0C: 0x0000}
        self.fd_active = False
        self.fd_bits: list[int] = []
        self.fd_read_mode = False
        self.fd_read_addr = 0
        self.fd_read_index = 0
        self.fd_read_bit = 0
        self.fd_writes: list[tuple[int, int]] = []
        self.rx_dma = None  # opt-in; no fabricated RF reception by default
        self.adc_inputs = {0: 2048, 1: 2048}
        if adc_inputs:
            self.adc_inputs.update(adc_inputs)
        self.adc_isr = 0
        self.adc_result = 0
        self.nvic_enabled = 0
        self.nvic_pending = 0
        self.nvic_active = 0
        self.nvic_priority = [0] * 8
        self.key_events = sorted((ms * 96_000, key, down) for ms, key, down in key_events)
        self.next_key_event = 0
        self.key_events_applied = 0
        self.gpio_events = sorted((ms * 96_000, port.upper(), pin, level)
                                  for ms, port, pin, level in gpio_events)
        self.next_gpio_event = 0
        self.gpio_events_applied = 0
        self.accesses: Counter[tuple[str, int]] = Counter()
        self.unknown_accesses: Counter[tuple[str, int]] = Counter()
        self.recent_mmio: deque[dict[str, int | str]] = deque(maxlen=256)
        self.instructions = 0
        self.cycles = 0.0
        self.tim6_started_at = 0.0
        self.cpi = cpi
        self.systick_last_read = 0.0
        self.systick_pending = False
        self.last_pc = 0
        self.fault = None
        self.memory_writes = deque(maxlen=8192)

        blob = image.read_bytes()
        if len(blob) < 8 or len(blob) > FLASH_BYTES - (APP_BASE - FLASH_BASE):
            raise ValueError(f"unexpected app image size: {len(blob)} bytes")

        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        self.uc.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_M0)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_INVALID, self._invalid_memory)
        self.uc.mem_map(FLASH_BASE, FLASH_BYTES)
        self.uc.mem_write(FLASH_BASE, b"\xff" * (APP_BASE - FLASH_BASE))
        self.uc.mem_write(APP_BASE, blob)
        self.uc.mem_map(RAM_BASE, RAM_BYTES)
        for lo, hi in MMIO_RANGES:
            for page in range(lo, hi, 0x1000):
                self.uc.mmio_map(page, 0x1000, self._read, page, self._write, page)

        initial_sp = int.from_bytes(blob[:4], "little")
        reset = int.from_bytes(blob[4:8], "little")
        if not (APP_RAM_BASE <= initial_sp <= RAM_BASE + RAM_BYTES) or not (APP_BASE <= reset & ~1 < FLASH_BASE + FLASH_BYTES):
            raise ValueError(f"bad reset vectors: MSP=0x{initial_sp:08x}, reset=0x{reset:08x}")
        self.uc.reg_write(arm.UC_ARM_REG_SP, initial_sp)
        self.uc.reg_write(arm.UC_ARM_REG_LR, 0xFFFFFFF9)
        self.reset_pc = reset & ~1
        for key in keys:
            self.press_key(key)

    def attach_rx_bits(self, bits, sample_rate=85800, dma_mode="direct"):
        """Arm GPIO DMA with direct samples or measured-rate held GPIO.

        Measured mode models repeated reads of a GPIO waveform. It does not
        establish that the actual IF or DMA clocks are calibrated.
        """
        if dma_mode not in ("direct", "measured"):
            raise ValueError("invalid DMA clock mode")
        if dma_mode == "measured":
            bits = held_gpio_samples(bits, sample_rate, MEASURED_FREE_RUNNING_HZ)
            transfer_rate = MEASURED_FREE_RUNNING_HZ
        else:
            transfer_rate = sample_rate
        def write_byte(address, value):
            if not RAM_BASE <= address < RAM_BASE + RAM_BYTES:
                raise ValueError(f"DMA destination outside SRAM: 0x{address:08x}")
            self.uc.mem_write(address, bytes((value,)))
        self.rx_dma = GpioRxDma(write_byte, bits, transfer_rate)
        self.rx_dma.mode = dma_mode
        self.rx_dma.signal_rate_hz = sample_rate

    def cpu_registers(self):
        names = [f"R{i}" for i in range(13)] + ["SP", "LR", "PC", "XPSR"]
        return {name.lower(): f"0x{self.uc.reg_read(getattr(self.arm, 'UC_ARM_REG_' + name)):08x}"
                for name in names}

    def _invalid_memory(self, uc, access, address, size, value, _data):
        self.fault = {"kind": "invalid_memory", "access": access,
                      "address": f"0x{address:08x}", "size": size,
                      "registers": self.cpu_registers()}
        return False

    def watch_write(self, address: int, size: int):
        """Bounded CPU-write history for a chosen object; never dumps arbitrary RAM."""
        if not RAM_BASE <= address < address + size <= RAM_BASE + RAM_BYTES:
            raise ValueError("write watch must lie in SRAM")
        def record(uc, access, at, width, value, data):
            self.memory_writes.append({"address": f"0x{at:08x}", "size": width,
                "value": f"0x{value:0{width * 2}x}",
                "pc": f"0x{uc.reg_read(self.arm.UC_ARM_REG_PC):08x}",
                "sp": f"0x{uc.reg_read(self.arm.UC_ARM_REG_SP):08x}",
                "instruction": self.instructions})
        return self.uc.hook_add(self.ucmod.UC_HOOK_MEM_WRITE, record,
                               begin=address, end=address + size - 1)

    def _gpio_idr(self, name: str) -> int:
        # Key columns are pulled high; the active-low row scan closes a pressed matrix key.
        col_pins = {0: ("C", 13), 1: ("C", 14), 2: ("F", 6), 3: ("B", 14)}
        base = next((addr for addr, port in GPIO_BASES.items() if port == name), 0)
        mode, pull = self.regs.get(base, 0), self.regs.get(base + 0x0C, 0)
        value = 0
        for pin in range(16):
            if ((mode >> (pin * 2)) & 3) != 1 and ((pull >> (pin * 2)) & 3) == 1:
                value |= 1 << pin
        for (port, pin), level in self.gpio_external.items():
            if port == name:
                value = (value | (1 << pin)) if level else (value & ~(1 << pin))
        if name == "B" and not (self.regs.get(0x48000400, 0) & (3 << 12)):
            value = (value & ~(1 << 6)) | (self.fd_read_bit << 6)
        row_pins = {1: 0, 2: 4, 3: 10, 4: 11, 5: 9}
        # An idle-row column read begins a keypad scan. Count only once until
        # a different row is driven (side-key scan can end early).
        rows = self.gpio_odr["B"] & 0xe11
        if name == "C" and rows == 0xe11 and self.last_key_probe != 0xe11:
            self.key_scan_epoch += 1
        if name in ("C", "F", "B"):
            self.last_key_probe = rows
        for row, col in self.key_matrix:
            if row == 0:  # side keys are sensed with the matrix rows idle
                pin = col_pins[col]
                if pin[0] == name:
                    value &= ~(1 << pin[1])
                    if self.key_sampled_scan != self.key_scan_epoch:
                        self.key_sample_epoch += 1
                        self.key_sampled_scan = self.key_scan_epoch
            elif not (self.gpio_odr["B"] & (1 << row_pins[row])):
                port, pin = col_pins[col]
                if port == name:
                    value &= ~(1 << pin)
                    if self.key_sampled_scan != self.key_scan_epoch:
                        self.key_sample_epoch += 1
                        self.key_sampled_scan = self.key_scan_epoch
        return value

    def set_gpio(self, port: str, pin: int, level: int):
        """Set an external digital pin level (0 or 1)."""
        if level not in (0, 1):
            raise ValueError("GPIO level must be 0 or 1")
        self.gpio_external[(port.upper(), pin)] = level

    def _apply_due_events(self):
        """Apply timestamped inputs as virtual time advances, including inside busy-waits.

        SysTick emulation can advance virtual time from an MMIO callback while Unicorn is still
        executing a quantum. Applying the whole input queue only after that quantum collapses
        every event crossed by the wait into one instant; short key presses then disappear
        before the firmware gets a chance to scan the keypad.
        """
        while self.next_key_event < len(self.key_events):
            at, key, down = self.key_events[self.next_key_event]
            if self.cycles < at:
                break
            self.press_key(key, down)
            self.next_key_event += 1
            self.key_events_applied += 1
        while self.next_gpio_event < len(self.gpio_events):
            at, port, pin, level = self.gpio_events[self.next_gpio_event]
            if self.cycles < at:
                break
            self.set_gpio(port, pin, level)
            self.next_gpio_event += 1
            self.gpio_events_applied += 1

    def inject_uart_rx(self, data: bytes):
        """Queue bytes from the programming-cable host for the virtual USART1 receiver."""
        self.uart_rx.extend(data)

    def _service_uart_irq(self):
        """Run Aura's USART1 ISR for pending receive or transmit work.

        The virtual transmitter is always ready. The ISR drains one queued
        byte per call and disables TXEIE when its transmit ring is empty.
        Preserve the interrupted CPU context across the targeted ISR call.
        """
        control = self.regs.get(USART1, 0)
        rx_pending = bool(self.uart_rx) and bool(control & (1 << 5))
        tx_pending = bool(control & (1 << 7))
        if not (rx_pending or tx_pending) or not (self.nvic_enabled & USART1_IRQ_BIT):
            return
        if self.uc.reg_read(self.arm.UC_ARM_REG_PRIMASK) & 1:
            return
        if self.uart_irq_handler is None:
            vector = APP_BASE + 4 * USART1_VECTOR_INDEX
            self.uart_irq_handler = int.from_bytes(self.uc.mem_read(vector, 4), "little")
        handler = self.uart_irq_handler
        if handler in (0, 0xFFFFFFFF):
            return

        regs = [getattr(self.arm, f"UC_ARM_REG_R{i}") for i in range(13)]
        regs += [self.arm.UC_ARM_REG_SP, self.arm.UC_ARM_REG_LR,
                 self.arm.UC_ARM_REG_PC, self.arm.UC_ARM_REG_XPSR,
                 self.arm.UC_ARM_REG_PRIMASK]
        saved = [(reg, self.uc.reg_read(reg)) for reg in regs]
        sentinel = RAM_BASE
        self.uc.reg_write(self.arm.UC_ARM_REG_LR, sentinel | 1)
        try:
            self.uc.emu_start(handler, sentinel, count=2048)
        except self.ucmod.UcError as exc:
            self.fault = {"kind": "uart_irq_error", "message": str(exc),
                          "handler": f"0x{handler:08x}"}
        finally:
            for reg, value in saved:
                self.uc.reg_write(reg, value)

    def _fd_pin_change(self, old: int, new: int):
        scn_old, scn_new = (old >> 3) & 1, (new >> 3) & 1
        if scn_old and not scn_new:
            self.fd_active = True
            self.fd_bits.clear()
            self.fd_read_mode = False
            return
        if not self.fd_active:
            return
        if not scn_old and scn_new:
            if not self.fd_read_mode and len(self.fd_bits) >= 24:
                addr = 0
                value = 0
                for bit in self.fd_bits[:8]:
                    addr = (addr << 1) | bit
                for bit in self.fd_bits[8:24]:
                    value = (value << 1) | bit
                addr &= 0x7F
                self.fd_regs[addr] = value
                self.fd_writes.append((addr, value))
            self.fd_active = False
            return
        sck_rise = not ((old >> 5) & 1) and ((new >> 5) & 1)
        if sck_rise:
            sda_is_input = ((self.regs.get(0x48000400, 0) >> 12) & 3) == 0
            if self.fd_read_mode:
                if sda_is_input:
                    self.fd_read_bit = (self.fd_regs.get(self.fd_read_addr, 0) >>
                                        (15 - self.fd_read_index)) & 1 if self.fd_read_index < 16 else 0
            else:
                self.fd_bits.append((new >> 6) & 1)
                if len(self.fd_bits) == 8:
                    addr = 0
                    for bit in self.fd_bits:
                        addr = (addr << 1) | bit
                    self.fd_read_mode = bool(addr & 0x80)
                    self.fd_read_addr = addr & 0x7F
                    self.fd_read_index = 0

    def _fd_read_sampled(self):
        if self.fd_active and self.fd_read_mode:
            self.fd_read_index += 1

    def press_key(self, key: str, pressed: bool = True):
        # Row/column indices match the real key table in device/keypad.rs.
        names = {
            "SIDE1": (0, 1), "SIDE2": (0, 3),
            "7": (1, 0), "8": (1, 1), "#": (1, 2), "9": (1, 3),
            "4": (2, 0), "5": (2, 1), "0": (2, 2), "6": (2, 3),
            "1": (3, 0), "2": (3, 1), "*": (3, 2), "3": (3, 3),
            "MENU": (4, 0), "UP": (4, 1), "EXIT": (4, 2), "DOWN": (4, 3),
            "VM": (5, 0), "AB": (5, 1), "BAND": (5, 3),
        }
        name = key.upper()
        if name not in names:
            raise ValueError(f"unknown key {key!r}; valid keys: {', '.join(names)}")
        if pressed:
            self.key_matrix.add(names[name])
        else:
            self.key_matrix.discard(names[name])

    def _flash_transfer(self, byte: int) -> int:
        if self.flash_command is None:
            self.flash_command = byte
            self.flash_address_bytes.clear()
            self.flash_program_data.clear()
            self.flash_output_index = 0
            return 0xFF
        cmd = self.flash_command
        if cmd == 0x9F:
            value = (0x20, 0x40, 0x15)[self.flash_output_index % 3]
            self.flash_output_index += 1
            return value
        if cmd == 0x05:
            return 2 if self.flash_wel else 0
        if cmd in (0x03, 0x02, 0x20, 0xD8):
            if len(self.flash_address_bytes) < 3:
                self.flash_address_bytes.append(byte)
                if len(self.flash_address_bytes) == 3:
                    self.flash_address = int.from_bytes(self.flash_address_bytes, "big")
                return 0xFF
            if cmd == 0x03:
                value = self.flash_data[self.flash_address % len(self.flash_data)]
                self.flash_address += 1
                return value
            if cmd == 0x02:
                self.flash_program_data.append(byte)
        return 0xFF

    def _flash_select(self, selected: bool):
        if selected:
            self.flash_command = None
            return
        cmd = self.flash_command
        if cmd == 0x06:
            self.flash_wel = True
        elif cmd == 0x02 and self.flash_wel:
            page = self.flash_address & ~0xFF
            start = self.flash_address & 0xFF
            for i, value in enumerate(self.flash_program_data):
                at = page + ((start + i) & 0xFF)
                self.flash_data[at % len(self.flash_data)] &= value
            self.flash_wel = False
        elif cmd in (0x20, 0xD8) and self.flash_wel:
            size = 4096 if cmd == 0x20 else 65536
            base = self.flash_address & ~(size - 1)
            self.flash_data[base:base + size] = b"\xff" * size
            self.flash_wel = False

    def _trace(self, kind: str, address: int, value: int, size: int):
        self.accesses[(kind, address)] += 1
        self.recent_mmio.append({"kind": kind, "address": address, "value": value,
                                 "size": size, "instruction": self.instructions})
        if not self._modeled(address):
            self.unknown_accesses[(kind, address)] += 1
        if self.trace_mmio:
            print(f"MMIO {kind:3} 0x{address:08x} size={size} value=0x{value:08x}", file=sys.stderr)

    @staticmethod
    def _modeled(address: int) -> bool:
        if 0x48000000 <= address < 0x48002000 and address & 0x3FF in range(0, 0x2C, 4):
            return True
        if RCC <= address < RCC + 0x44 or USART1 <= address < USART1 + 0x2C:
            return True
        if SPI1 <= address < SPI1 + 0x24 or SPI2 <= address < SPI2 + 0x24:
            return True
        if (TIM6 <= address < TIM6 + 0x54 or TIM14 <= address < TIM14 + 0x54
                or TIM17 <= address < TIM17 + 0x54):
            return True
        if DMA <= address < DMA + 0x18:
            return True
        if ADC <= address <= ADC + 0x40:
            return True
        if address == FLASH_ACR:
            return True
        if address == SYSCFG_CFGR1:
            return True
        if address in (NVIC_ISER, NVIC_ICER, NVIC_ISPR, NVIC_ICPR, NVIC_IABR):
            return True
        if NVIC_IPR <= address < NVIC_IPR + 0x20:
            return True
        if 0xE000E010 <= address <= 0xE000E018:
            return True
        return False

    def _read(self, uc, offset, size, page):
        address = page + offset
        value = self.regs.get(address, 0)

        if self.rx_dma is not None and DMA <= address < DMA + 0x18:
            value = self.rx_dma.read(address)
        elif address == RCC:
            if value & (1 << 16):
                value |= 1 << 17             # HSE ready follows HSE on
            if value & (1 << 24):
                value |= 1 << 25             # PLL ready follows PLL on
        elif address == RCC + 4:
            value = (value & ~0xC) | ((value & 3) << 2)  # SWS follows SW
        elif address == USART1 + 0x1C:        # ISR: TXE, TC, and queued RXNE
            value |= (1 << 7) | (1 << 6)
            if self.uart_rx:
                value |= 1 << 5
        elif address == USART1 + 0x24:        # RDR
            value = self.uart_rx.popleft() if self.uart_rx else 0
        elif address in (SPI1 + 8, SPI2 + 8):
            value |= 1 << 1                  # TXE
            if address - 8 in self.spi_rx and self.spi_rx[address - 8]:
                value |= 1 << 0              # RXNE
        elif address in (SPI1 + 0xC, SPI2 + 0xC):
            base = address - 0xC
            value = self.spi_rx[base].popleft() if self.spi_rx[base] else 0xFF
        elif address == TIM14 + 0x24:
            value = int(self.cycles // 9600) & 0xFFFF
        elif address == TIM6 + 0x10:       # TIM6 UIF follows its prescaler and auto-reload
            value &= ~1
            if self.regs.get(TIM6, 0) & 1:
                divider = self.regs.get(TIM6 + 0x28, 0) + 1
                period = divider * (self.regs.get(TIM6 + 0x2C, 0) + 1)
                if self.cycles - self.tim6_started_at >= period:
                    value |= 1
        elif address == TIM17 + 0x24:
            value = int(self.cycles // (self.regs.get(TIM17 + 0x28, 0) + 1)) & 0xFFFF
        elif address == ADC + 0x00:
            value = self.adc_isr
        elif address == ADC + 0x40:
            value = self.adc_result
        elif address == NVIC_ISER:
            value = self.nvic_enabled
        elif address == NVIC_ICER:
            value = self.nvic_enabled
        elif address == NVIC_ISPR:
            value = self.nvic_pending
        elif address == NVIC_ICPR:
            value = self.nvic_pending
        elif address == NVIC_IABR:
            value = self.nvic_active
        elif NVIC_IPR <= address < NVIC_IPR + 0x20:
            value = self.nvic_priority[(address - NVIC_IPR) // 4]
        elif address == FLASH_ACR:
            value = self.regs.get(address, 0)
        elif address == 0xE000E010:           # SysTick CTRL; COUNTFLAG on a wrap
            value &= 0x7
            if value & 1 and self.systick_pending:
                self.cycles += self.regs.get(0xE000E014, 0) + 1
                value |= 1 << 16
                self.systick_last_read = self.cycles
                self.systick_pending = False
                self._apply_due_events()
        elif address == 0xE000E018:           # SysTick VAL
            reload = self.regs.get(0xE000E014, 0) + 1
            value = reload - int(self.cycles % reload) - 1 if reload > 1 else 0
        else:
            for base, name in GPIO_BASES.items():
                if address == base + 0x10:   # IDR from external pins and output latches
                    value = self._gpio_idr(name)
                    if name == "B" and not (self.regs.get(base, 0) & (3 << 12)):
                        self._fd_read_sampled()
                    break

        if self.collect_mmio:
            self._trace("read", address, value, size)
        return value

    def _write(self, uc, offset, size, value, page):
        address = page + offset
        if self.collect_mmio:
            self._trace("write", address, value, size)
        if address == 0xE000E018:             # writing SysTick VAL clears COUNTFLAG
            self.systick_last_read = self.cycles
            self.systick_pending = True
            self.regs[address] = 0
        elif address == TIM6 + 0x10:          # SR write clears UIF in the model
            self.regs[address] = value & ~1
            if value & 1 == 0:
                self.tim6_started_at = self.cycles
        elif address == TIM6 + 0x14 and value & 1:  # EGR.UG resets the timer counter
            self.regs[address] = value
            self.tim6_started_at = self.cycles
        elif address == SYSCFG_CFGR1:        # MEM_MODE: main/system flash or SRAM at address zero
            self.regs[address] = value & 0x3
            if not self.memory_alias_mapped:
                uc.mem_map(0, 0x1000)
                self.memory_alias_mapped = True
            mode = value & 0x3
            if mode == 3:
                alias = bytes(uc.mem_read(RAM_BASE, 0x1000))
            elif mode in (0, 2):
                alias = bytes(uc.mem_read(FLASH_BASE, 0x1000))
            else:
                alias = b"\xff" * 0x1000
            uc.mem_write(0, alias)
        elif address == USART1 + 0x28:        # TDR
            self.uart_tx.append(value & 0xFF)
            sys.stdout.buffer.write(bytes((value & 0xFF,)))
            sys.stdout.buffer.flush()
        elif address == ADC + 0x00:           # ISR flags are write-one-to-clear
            self.adc_isr &= ~(value & 0x3F)
        elif address == ADC + 0x08:           # CR: calibration and conversion self-clear
            control = value & ~(1 << 31)
            if value & (1 << 2):             # ADSTART
                selected = self.regs.get(ADC + 0x28, 0)
                channel = (selected & -selected).bit_length() - 1 if selected else 0
                self.adc_result = self.adc_inputs.get(channel, 0) & 0x0FFF
                self.adc_isr |= 1 << 2        # EOC
                control &= ~(1 << 2)
            if value & 1:                    # ADEN -> ADRDY
                self.adc_isr |= 1
            self.regs[address] = control
        elif address == NVIC_ISER:
            self.nvic_enabled |= value
        elif address == NVIC_ICER:
            self.nvic_enabled &= ~value
        elif address == NVIC_ISPR:
            self.nvic_pending |= value
        elif address == NVIC_ICPR:
            self.nvic_pending &= ~value
        elif NVIC_IPR <= address < NVIC_IPR + 0x20:
            self.nvic_priority[(address - NVIC_IPR) // 4] = value & 0xFFFFFFFF
        elif address in (SPI1 + 0xC, SPI2 + 0xC):
            base = address - 0xC
            self.spi_tx[base].append(value & 0xFF)
            if base == SPI1:
                self.spi_rx[base].append(self._flash_transfer(value & 0xFF))
            else:
                self.spi_rx[base].append(0xFF)
                dc = (self.gpio_odr["C"] >> 15) & 1
                cs = (self.gpio_odr["B"] >> 12) & 1
                self.lcd_trace.append((self.cycles, dc, cs, value & 0xFF))
                self.screen.write(dc, cs, value & 0xFF)
        else:
            previous_power_latch = self.gpio_odr["A"] & (1 << 8)
            for base, name in GPIO_BASES.items():
                if address == base + 0x14:    # ODR
                    old = self.gpio_odr[name]
                    self.gpio_odr[name] = value & 0xFFFF
                    if name == "A" and bool(old & 4) != bool(value & 4):
                        self._flash_select(not bool(value & 4))
                    if name == "B":
                        self._fd_pin_change(old, self.gpio_odr[name])
                    break
                if address == base + 0x18:    # BSRR
                    if name == "B" and value & 0xe11 == 0xe11:
                        self.last_key_probe = -1
                    old = self.gpio_odr[name]
                    self.gpio_odr[name] |= value & 0xFFFF
                    self.gpio_odr[name] &= ~((value >> 16) & 0xFFFF)
                    self.gpio_odr[name] &= 0xFFFF
                    if name == "A" and bool(old & 4) != bool(self.gpio_odr[name] & 4):
                        self._flash_select(not bool(self.gpio_odr[name] & 4))
                    if name == "B":
                        self._fd_pin_change(old, self.gpio_odr[name])
                    break
                if address == base + 0x28:    # BRR
                    old = self.gpio_odr[name]
                    self.gpio_odr[name] &= ~(value & 0xFFFF)
                    if name == "A" and bool(old & 4) != bool(self.gpio_odr[name] & 4):
                        self._flash_select(not bool(self.gpio_odr[name] & 4))
                    if name == "B":
                        self._fd_pin_change(old, self.gpio_odr[name])
                    break
            else:
                self.regs[address] = value & ((1 << (8 * size)) - 1)
            # Open knob (PA15) and released latch (PA8) remove board power.
            if previous_power_latch and not self.gpio_odr["A"] & (1 << 8) and self.gpio_external[("A", 15)]:
                self.powered_off = True
                uc.emu_stop()

    def run(self, count: int, quantum: int = 256):
        """Execute at most `count` Thumb instructions; timers advance between small quanta."""
        if count < 0 or quantum <= 0:
            raise ValueError("count must be nonnegative and quantum positive")
        if self.fault or self.powered_off:
            return self.last_pc
        arm = self.arm
        remaining = count
        while remaining > 0:
            n = min(quantum, remaining)
            begin = self.reset_pc | 1 if self.instructions == 0 else self.uc.reg_read(arm.UC_ARM_REG_PC) | 1
            try:
                self.uc.emu_start(begin, 0, count=n)
            except self.ucmod.UcError as exc:
                pc = self.uc.reg_read(arm.UC_ARM_REG_PC)
                sp = self.uc.reg_read(arm.UC_ARM_REG_SP)
                lr = self.uc.reg_read(arm.UC_ARM_REG_LR)
                print(f"\nEMU STOP at PC=0x{pc:08x} SP=0x{sp:08x} LR=0x{lr:08x}: {exc}", file=sys.stderr)
                self.last_pc = pc
                if self.fault is None:
                    self.fault = {"kind": "cpu_error", "message": str(exc),
                                  "registers": self.cpu_registers()}
                break
            if self.powered_off:
                self.last_pc = self.uc.reg_read(arm.UC_ARM_REG_PC) & ~1
                break
            self.instructions += n
            self.cycles += n * self.cpi
            if self.rx_dma is not None:
                self.rx_dma.advance(n * self.cpi)
            self._apply_due_events()
            self._service_uart_irq()
            self.last_pc = self.uc.reg_read(arm.UC_ARM_REG_PC) & ~1
            remaining -= n
        return self.uc.reg_read(arm.UC_ARM_REG_PC)

    def summary(self):
        print(f"\nK6 emulator: {self.instructions:,} instructions; ~{self.cycles / 96_000:.1f} ms virtual; "
              f"PC=0x{self.last_pc:08x}; "
              f"UART TX={len(self.uart_tx)} bytes; SPI1={len(self.spi_tx[SPI1])} bytes; "
              f"SPI2={len(self.spi_tx[SPI2])} bytes; key events={self.key_events_applied}; "
              f"GPIO events={self.gpio_events_applied}",
              file=sys.stderr)
        if self.accesses:
            print("MMIO hot spots:", file=sys.stderr)
            for (kind, addr), n in self.accesses.most_common(12):
                print(f"  {kind:5s} 0x{addr:08x}: {n}", file=sys.stderr)
        if self.unknown_accesses:
            print("Unmodelled MMIO accesses (generic register storage/default reads):", file=sys.stderr)
            for (kind, addr), n in self.unknown_accesses.most_common(12):
                print(f"  {kind:5s} 0x{addr:08x}: {n}", file=sys.stderr)

    def write_report(self, path: Path, image: Path, mode: str, modules: list | None = None):
        """Write a bounded, machine-readable snapshot for repeatable debugging."""
        screen_ram, screen_reverse = self.screen.snapshot()
        report = {
            "schema": "k6emu.report.v1",
            "image": str(image),
            "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "fault": self.fault,
            "registers": self.cpu_registers(),
            "memory_writes": list(self.memory_writes),
            "run": {
                "mode": mode,
                "instructions": self.instructions,
                "cycles_estimate": self.cycles,
                "virtual_ms_estimate": round(self.cycles / 96_000, 3),
                "pc": f"0x{self.last_pc:08x}",
                "key_events_applied": self.key_events_applied,
                "gpio_events_applied": self.gpio_events_applied,
            },
            "uart": {
                "bytes_total": len(self.uart_tx),
                "tail_hex": bytes(self.uart_tx[-4096:]).hex(" "),
                "tail_text": bytes(self.uart_tx[-4096:]).decode("utf-8", errors="replace"),
            },
            "spi_tx_bytes": {
                "spi1": len(self.spi_tx[SPI1]),
                "spi2": len(self.spi_tx[SPI2]),
            },
            "mmio": {
                "hotspots": [{"kind": kind, "address": f"0x{address:08x}", "count": count}
                             for (kind, address), count in self.accesses.most_common(32)],
                "unmodelled": [{"kind": kind, "address": f"0x{address:08x}", "count": count}
                               for (kind, address), count in self.unknown_accesses.most_common(64)],
                "recent": [{**event, "address": f"0x{event['address']:08x}",
                            "value": f"0x{event['value']:08x}"} for event in self.recent_mmio],
            },
            "fd6818_writes": [{"register": f"0x{reg:02x}", "value": f"0x{value:04x}"}
                               for reg, value in self.fd_writes[-256:]],
            "screen": {
                "reverse": screen_reverse,
                "ram_hex": bytes(value for row in screen_ram for value in row).hex(),
                "lit_pixels_in_controller_ram": sum(value.bit_count()
                                                     for row in screen_ram for value in row),
            },
            "lcd_trace_events": len(self.lcd_trace),
            "modules": modules or [],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Wrote emulator debug report: {path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path, help="raw K6 application image; vector table at offset 0")
    ap.add_argument("--instructions", type=int, default=2_000_000,
                    help="maximum instructions to execute (default: 2 million)")
    ap.add_argument("--trace-mmio", action="store_true", help="print every virtual register access")
    ap.add_argument("--spi-flash", type=Path, help="seed 2 MiB external flash from a k6_install.py backup")
    ap.add_argument("--module-bundle", type=Path,
                    help="sign and overlay bundle modules into virtual SPI library slots")
    ap.add_argument("--module-slots", metavar="SLOTS",
                    help="comma-separated module slots from --module-bundle; default overlays all modules")
    ap.add_argument("--key-file", type=Path,
                    help="module signing key for --module-bundle (uses the Flux default key if omitted)")
    ap.add_argument("--save-spi", type=Path, help="write the virtual SPI flash after execution")
    ap.add_argument("--press-key", action="append", default=[], metavar="KEY",
                    help="hold a virtual keypad key while the firmware runs; repeatable")
    ap.add_argument("--adc", action="append", default=[], metavar="CHANNEL=RAW",
                    help="set an ADC channel input (0..18, 0..4095); repeatable, defaults to 2048")
    ap.add_argument("--key-event", action="append", default=[], metavar="MS:KEY:down|up",
                    help="timestamped key event, e.g. 500:MENU:down; repeatable")
    ap.add_argument("--gpio-event", action="append", default=[], metavar="MS:PORT:PIN:low|high",
                    help="drive a virtual external GPIO, e.g. 1000:A:10:low for PTT press")
    ap.add_argument("--lcd-trace", type=Path,
                    help="save timestamped LCD SPI bytes as JSONL for display reconstruction")
    ap.add_argument("--report-json", type=Path,
                    help="write a bounded machine-readable debug snapshot (UART, PC, MMIO, screen)")
    ap.add_argument("--live", action="store_true",
                    help="open a live LCD window; use keyboard for keypad/PTT input")
    ap.add_argument("--live-slice", type=int, default=20_000,
                    help="instructions per live-run scheduling slice (default: 20000)")
    ap.add_argument("--cpi", type=float, default=1.5,
                    help="estimated Cortex-M0 cycles per instruction (default: 1.5)")
    ap.add_argument("--quantum", type=int, default=256,
                    help="instruction steps between virtual timer updates (default: 256)")
    args = ap.parse_args()
    key_events = []
    for spec in args.key_event:
        try:
            ms, key, action = spec.split(":")
            if action not in ("down", "up") or float(ms) < 0:
                raise ValueError
            key_events.append((float(ms), key, action == "down"))
        except ValueError:
            ap.error(f"invalid --key-event {spec!r}; use MS:KEY:down|up")
    adc_inputs = {}
    for spec in args.adc:
        try:
            channel_text, raw_text = spec.split("=", 1)
            channel, raw = int(channel_text, 0), int(raw_text, 0)
            if not (0 <= channel <= 18 and 0 <= raw <= 4095):
                raise ValueError
            adc_inputs[channel] = raw
        except ValueError:
            ap.error(f"invalid --adc {spec!r}; use CHANNEL=RAW with channel 0..18 and raw 0..4095")
    gpio_events = []
    for spec in args.gpio_event:
        try:
            ms_text, port, pin_text, level_text = spec.split(":")
            ms, pin = float(ms_text), int(pin_text, 0)
            port = port.upper()
            if (ms < 0 or port not in GPIO_BASES.values() or not 0 <= pin <= 15
                    or level_text.lower() not in ("low", "high")):
                raise ValueError
            gpio_events.append((ms, port, pin, 0 if level_text.lower() == "low" else 1))
        except ValueError:
            ap.error(f"invalid --gpio-event {spec!r}; use MS:PORT:PIN:low|high")
    module_slots = None
    if args.module_slots is not None:
        try:
            module_slots = {int(part, 0) for part in args.module_slots.split(",")}
            if not module_slots or any(not 0 <= slot < 32 for slot in module_slots):
                raise ValueError
        except ValueError:
            ap.error("--module-slots must be comma-separated slot numbers from 0 through 31")
        if not args.module_bundle:
            ap.error("--module-slots requires --module-bundle")
    board = K6Board(args.image, trace_mmio=args.trace_mmio, spi_image=args.spi_flash,
                     keys=tuple(args.press_key), key_events=tuple(key_events),
                     gpio_events=tuple(gpio_events), adc_inputs=adc_inputs, cpi=args.cpi)
    if args.module_bundle:
        try:
            tool_dir = str(Path(__file__).resolve().parents[2] / "tools")
            if tool_dir not in sys.path:
                sys.path.insert(0, tool_dir)
            import k6_install
            key_path = Path(k6_install.find_key(str(args.key_file) if args.key_file else None,
                                                create=False, log=lambda _: None))
            installed = overlay_module_bundle(board.flash_data, args.module_bundle,
                                               key_path, module_slots)
        except Exception as exc:
            ap.error(f"could not overlay module bundle: {exc}")
        print("Virtual SPI modules: " + ", ".join(
            f"slot {slot} {name} ({size} B)" for slot, name, size in installed), file=sys.stderr)
    print(f"Booting {args.image} at 0x{APP_BASE:08x}; RAM 0x{APP_RAM_BASE:08x}.."
          f"0x{RAM_BASE + RAM_BYTES:08x}", file=sys.stderr)
    if args.live:
        if args.live_slice <= 0:
            ap.error("--live-slice must be positive")
        from .live import run_live
        run_live(board, quantum=args.quantum, slice_instructions=args.live_slice)
    else:
        board.run(args.instructions, args.quantum)
    board.summary()
    if args.save_spi:
        args.save_spi.write_bytes(board.flash_data)
        print(f"Wrote virtual SPI flash: {args.save_spi}", file=sys.stderr)
    if args.lcd_trace:
        args.lcd_trace.write_text("".join(json.dumps({"cycles": t, "dc": dc, "cs": cs, "byte": b}) + "\n"
                                               for t, dc, cs, b in board.lcd_trace))
        print(f"Wrote {len(board.lcd_trace)} LCD SPI events: {args.lcd_trace}", file=sys.stderr)
    if args.report_json:
        report_modules = ([{"slot": slot, "name": name, "image_bytes": size}
                          for slot, name, size in installed] if args.module_bundle else [])
        board.write_report(args.report_json, args.image, "live" if args.live else "headless",
                           report_modules)


if __name__ == "__main__":
    main()
