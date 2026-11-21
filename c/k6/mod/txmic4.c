/* txmic4: experimental MICIN->RF bench path using Flux API level 26.
   Default build is intentionally INERT. No external microphone source is
   created by this module. Enable only with an isolated MICIN fixture,
   rated 50-ohm dummy load, attenuated RF measurement path and verified
   independent TX watchdog. No claims for public on-air operation.
   The board and older FD6818 register differences are NOT assumed.
   SPDX-License-Identifier: MIT */
#ifndef TXMIC_ENABLE
#define TXMIC_ENABLE 0
#endif
#ifndef TXMIC_FIXTURE_ACK
#define TXMIC_FIXTURE_ACK 0
#endif
#ifndef TXMIC_WATCHDOG_ACK
#define TXMIC_WATCHDOG_ACK 0
#endif
#if TXMIC_ENABLE && (!TXMIC_FIXTURE_ACK || !TXMIC_WATCHDOG_ACK)
#error "MICIN TX requires explicit fixture AND watchdog acknowledgment"
#endif
#define K6_MODULE_LEVEL 26 /* always require the experimental ABI */
#include "../k6_mod.h"

#define TEST_HZ 146560000u
#define REG_AF_FILTER 0x2bu
#define TEST_MAX_MS 750u
#define BIAS_PREP_MS 250u
#define RF_BURST_MS 100u

int k6_main(const k6_api_t *api) {
  if (!api || api->abi != 1 || api->level < K6_MODULE_LEVEL) return -3;
  if (api->fd_read(0u) != 0x4829u) return -6;
#if !TXMIC_ENABLE
  api->say("txmic4 inert: fixture required");
  return -9;
#else
  int ret=0, keyed=0, mic=0, filter_set=0, pa=0;
  unsigned i;
  uint16_t original=0u, dc;
  if (api->rx_hz()!=TEST_HZ || api->key_down()) return -2;
  if (KM_RSSI_DBM(api->fd_read(0x67)) > -85) return -7;
  api->delay_us(50000);
  if (KM_RSSI_DBM(api->fd_read(0x67)) > -85) return -7;
  if (api->tx_on(TEST_MAX_MS) != 0) return -5;
  keyed=1;
  if (api->tx_pa(0) < 0) { ret=-4; goto done; }
  if (api->micin_tx(1) != 0) { ret=-13; goto done; }
  mic=1;
  if (api->fd_read(0x30u)!=0xC1FEu) { ret=-14; goto done; }
  original=api->fd_read(REG_AF_FILTER);
  dc=api->fd_read(0x7Eu);
  if (api->fd_write(REG_AF_FILTER, (uint16_t)(original | 7u)) != 0) {
    ret=-8; goto done;
  }
  filter_set=1;
  if (api->fd_read(REG_AF_FILTER) != (uint16_t)(original|7u)) {
    ret=-8; goto done;
  }
  /* Allow the MICIN ADC bias to settle with the RF PA disabled. The
     250-ms figure is a lab hypothesis from the original 0x4819 design. */
  for (i=0;i<BIAS_PREP_MS/5u;i++) {
    if (api->key_down() || api->tx_pa(0)<0) {ret=-4;goto done;}
    api->delay_us(5000u);
  }
  if (api->fd_read(0x7Eu)!=dc) {ret=-15;goto done;}
  if (api->tx_pa(1)<0) {ret=-4;goto done;}
  pa=1;
  if (api->fd_read(0x30u)!=0xC1FEu) {ret=-16;goto done;}
  for (i=0;i<RF_BURST_MS/5u;i++) {
    if (api->key_down()) {ret=-4;break;}
    api->delay_us(5000u);
  }
done:
  if (pa || keyed) (void)api->tx_pa(0);
  if (filter_set) {
    if (api->fd_write(REG_AF_FILTER, original)!=0 ||
        api->fd_read(REG_AF_FILTER)!=original) ret=-10;
  }
  if (mic && api->micin_tx(0)!=0) ret=-11;
  if (keyed) api->tx_off();
  if (ret == 0) api->say("txmic4 bench done; RX restored");
  else api->say("txmic4 abort; check RX cleanup");
  return ret;
#endif
}
