/* Experimental one-shot FD6818B native FSK RF test. Launching this module sends
   one frame; no extra MENU confirmation or dummy-load requirement. The core
   retains its TX interlocks and timeout. Not a general-purpose unattended beacon.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 15
#define K6_MODULE_OPT z
#include "k6ui.h"
K6_MODULE_NAME("FSK LAB TX");

static int ham_70cm(uint32_t hz) { return hz >= 420100000u && hz <= 449900000u; }

int k6_main(const k6_api_t *a) {
  uint8_t settings[KM_SETTINGS_BYTES];
  uint16_t words[8] = {0x4147,0x3753,0x5520,0x4653,0x4b20,0x55aa,0xaa55,0x0123};
  uint32_t hz;
  int r;
  if (a->level < 15 || a->fd_read(0) != 0x4829) return -3;
  hz = a->rx_hz();
  if (!ham_70cm(hz)) { a->say("FSK lab: select 70cm VFO"); return -4; }
  if (a->settings_read(settings, sizeof settings) != sizeof settings ||
      settings[KM_SETTINGS_TX_FORBID]) { a->say("FSK lab: TX locked"); return -5; }
  if (a->fsk_rx_start() != 0) { a->say("FSK lab: Data unavailable"); return -6; }
  a->fsk_rx_stop();
  if (a->tx_on(1500) != 0) { a->say("FSK lab: TX refused"); return -7; }
  r = a->fsk_tx_send(words);
  a->tx_off();
  a->say(r ? "FSK lab: frame failed" : "FSK lab: frame sent");
  return r;
}
