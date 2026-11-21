/* txbp4: DEPRECATED filter-only TX audio probe. Do not enable.
   The existing module tx_on deliberately disables MICIN ADC, so this module
   cannot validate a MICIN waveform. Use txmic4 and the level-26 API instead.
   Source file is inert unless explicitly compiled with two independent flags.
   It does NOT create audio: a separately isolated, level-limited MICIN stimulus
   is required. PA-off can leak RF; always use a rated dummy load + pickoff.
   No undocumented REG_7E write, no EEPROM/calibration/flash, no raw PA writes.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TXBP_ENABLE
#define TXBP_ENABLE 0
#endif
#ifndef TXBP_FIXTURE_ACK
#define TXBP_FIXTURE_ACK 0
#endif
#ifndef TXBP_HZ
#define TXBP_HZ 146560000u
#endif
#if TXBP_ENABLE && !TXBP_FIXTURE_ACK
#error "TXBP requires explicit bench load and attenuator acknowledgment"
#endif
#if TXBP_ENABLE
#error "Legacy TXBP has MICIN ADC disabled; use txmic4 with level-26 firmware"
#endif
#define REG_TX_AF_FILTER 0x2Bu
#define BYPASS_MASK 0x0007u
#define TEST_MAX_MS 800u
#define TEST_HOLD_MS 200u

int k6_main(const k6_api_t *api) {
  if (!api || api->abi != 1 || api->level < 3) return -3;
  if (api->fd_read(0u) != 0x4829u) return -6;
#if !TXBP_ENABLE
  api->say("txbp4 inert: fixture-gated");
  return -9;
#else
  uint16_t original;
  int ret = 0, keyed = 0, changed = 0;
  unsigned i;
  if (api->rx_hz() != TXBP_HZ || api->key_down()) return -2;
  if (KM_RSSI_DBM(api->fd_read(0x67u)) > -85) return -7;
  api->delay_us(50000u);
  if (KM_RSSI_DBM(api->fd_read(0x67u)) > -85) return -7;
  if (api->tx_on(TEST_MAX_MS) != 0) return -5;
  keyed = 1;
  if (api->tx_pa(0) < 0) { ret=-5; goto done; }
  original = api->fd_read(REG_TX_AF_FILTER);
  /* Disable ONLY TX voice HPF, LPF1 and pre-emphasis. Preserve RX bits. */
  if (api->fd_write(REG_TX_AF_FILTER, (uint16_t)(original|BYPASS_MASK)) != 0) {
    ret = -8; goto done;
  }
  changed = 1;
  api->say("txbp4 bypass set no PCM yet");
  for (i=0;i<TEST_HOLD_MS/5u;i++) {
    if (api->key_down() || api->tx_pa(0)<0) {ret=-4;break;}
    api->delay_us(5000u);
  }
done:
  if (changed && api->fd_write(REG_TX_AF_FILTER,original) != 0) ret=-10;
  if (keyed) api->tx_off();
  if (ret == -10) api->say("txbp4 RESTORE FAILED; check radio");
  else api->say(ret==0 ? "txbp4 done restored" : "txbp4 aborted restored");
  return ret;
#endif
}
