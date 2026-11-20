# Flux RF Laboratory (experimental)

These slot-independent local modules do not require a base-image change. Install by
name from the local module catalog; use a cable serial log for detailed results.
They have not been validated on physical radios. Do not infer RF coverage or
interoperability from a module compiling or a register readback.

| Module | Radio | Required service | Behavior |
|---|---|---|---|
| RF LIMITS | FD6818 (0x4819) / FD6818B (0x4829) | API 4 | RX only; original frequency and 20 test frequencies, narrow FM |
| RF FSK RX | FD6818B | API 10 + Data | RX only, 60 s, raw 8-word FIFO frames |
| RF FSK TX | FD6818B | API 15 + Data | one confirmed low-power frame, selected 2m/70cm frequency; dummy load only |
| RF IF B | FD6818B | API 4 | serial-only existing IF-selector/GP0 capture survey, with chip-ID guard |

RF LIMITS: after releasing the launch key, runs automatically. Any key stops.
The serial header gives the chip ID; each sample line gives frequency in Hz, raw 0x0c status at the
frequency and +30 kHz, and nominal RSSI readings at both points. Bit 9 is a
candidate PLL-lock indicator from the generic guide, not validated on either
register-map variant. Verify with a known external carrier; compare on and off
frequency, source off, and multiple source levels. Raw RSSI calibration may vary
by chip. Existing original-chip bench tests found real reception approximately
18–630 MHz; the generic guide's 740–1120 MHz range has not been established
on either K6's antenna port. Both variants use their existing host tune path.
No TX or writes to arbitrary registers.

RF FSK: use two B radios with the Data feature enabled; choose the same clear
amateur test frequency well inside 2m or 70cm, use dummy loads and a suitably
attenuated conducted connection. Start the RX listener first, then TX. TX
requires a valid configured APRS callsign and TX enabled, checks Data availability
with the RX callback, requires release and a fresh MENU press within 15 s, and
sends once with the host's guarded TX API. EXIT cancels. No automatic retry.
The host's native FSK service selects 1200 baud for both RX and TX; this is a
baseline, not proof of another rate or wire protocol. Frame is eight words:
three words containing up to six ASCII callsign characters (space padded),
0x5246 ("RF"), 0x55AA, 0xAA55, 0x0123, 0x4567. Verify the captured word/byte
order independently. RX reports every FIFO frame and checks only the last
four pattern words; lost sync frames are not counted, so do not call this BER.
Use dummy load only; do not assume the on-air mapping or ID compliance before
testing. Native FSK TX is deliberately unavailable outside 2m/70cm and on
the original chip; no extra TX ranges, raw PA writes or bypasses are added.

RF IF B: use a steady, reasonably strong reference carrier (start around
-70 dBm at the radio input, adjust carefully). The existing IF survey compares
the inherited selector and Aura FM selector, filter width and AFC states and
reports measured IF, min/max, accepted and lost blocks. Observe serial;
any key stops. No new register recipe or calibration flash write. See
k6-fd6818b-refcal.md and k6-aprs-b-rx-audit.md. The original chip has existing
bandreg, hfprobe, rangerx, gp0dma and related diagnostics; no B register recipe
is transferred to it.

## Bench hardware

Never connect any handheld transmitter directly to either SDR or the MFJ.
A nominal 5 W transmitter is +37 dBm; use a 50-ohm dummy load rated for
the transmitter's worst-case power, directional coupler rated at the test
frequency, and sufficient fixed attenuation on the coupled port (e.g. 60–70 dB
total including coupling, yielding -23 to -33 dBm at 5 W). Verify each component's
rating, real attenuation vs frequency and SDR input specification first.
Disable receiver bias tees, AGC and use low/fixed gain; check for overload
by adding another 10 dB pad and confirming the measured signal drops 10 dB.
Don't terminate the radio in the SDR or a small signal-rated attenuator.

* RSPduo: best for relative spectrum, frequency error, deviation and occupied
  bandwidth (1 kHz–2 GHz, 0 dBm continuous maximum input). Dual tuner can
  monitor fundamental and a harmonic with separately safe sampled paths.
* RTL-SDR: supplementary receiver for IQ capture, relative spectra and
  decoded FSK. Exact coverage and maximum input depend on model; do not
  use maximum absolute power as a target.
* MFJ-225: HF/VHF antenna/network analyzer, not a calibrated spectrum analyzer.
  Its independent DDS output (nominal -5 dBm; manufacturer rates 1.8–170 MHz)
  makes it useful as a reference carrier for the low-frequency RX tests.
  Start with 70 dB or more verified attenuation, measure actual output and
  add pads as needed for weak signals. Never key the K6 into it. Does not
  provide a 220, 440 or 915 MHz reference; an additional signal source is
  needed there. Hacking this instrument is a separate investigation.

Suggested bench order:
1. Run RF LIMITS with no source on both chip types; save serial.
2. Repeat with a known attenuated carrier at 16, 18, 29, 50 and 145 MHz
   using the MFJ output. A local oscillator spur alone is not confirmation.
3. Repeat at 223, 433, 600, 630 and 915 MHz using a suitable RF generator
   or a controlled legal test source; start with RX only.
4. Capture single native FIFO TX test at the RSPduo with a conducted load;
   measure center, spectrum, symbol rate and deviation; repeat on RTL-SDR
   if available. Test B-to-B reception and compare actual raw words.
5. Run RF IF B with a stable carrier to investigate baseband. Subsequent
   work: characterized rate/deviation sweep and 4FSK/M17 feasibility,
   audio chirps. No LoRa or M17 implementation is claimed here.

For a full spectrum scan, sweep the receiver frequency in separate acquisitions
without changing the radio's transmit frequency. The SDR is not a calibrated
spectrum analyzer; absolute power, spur levels and emission compliance need
a known/calibrated chain with enough dynamic range.

See [Extra-low-power laboratory findings](k6-extra-low-power-lab.md) for the separate, hardware-tested opt-in serial TX experiment. The module descriptions above retain their original constraints.
