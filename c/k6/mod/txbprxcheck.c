/* txbprxcheck: receive-only register RMW/restore test on the actual FD6818B.
   No TX, PTT, PA, power, frequency, GPIO, SPI or calibration writes.
   Only an exact known RX baseline REG_2B=0x0300 may be briefly changed to
   0x0307, checked, and restored. Returning from a RAM module restores
   the host RX state too; this module restores explicitly before returning.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"

#define CHIP_ID 0x4829u
#define RX_STATE 0xBFF1u
#define RX_HZ 146560000u
#define FILTER_REG 0x2Bu
#define FILTER_BASE 0x0300u
#define FILTER_TEST 0x0307u

int k6_main(const k6_api_t *api) {
  uint16_t after, final, state, dc_before, dc_after;
  int wrote = 0, restored = 0;
  if (!api || api->abi != 1 || api->level < 2) return -3;
  if (api->fd_read(0u) != CHIP_ID) return -6;
  if (api->rx_hz() != RX_HZ || api->key_down()) return -2;
  state = api->fd_read(0x30u);
  if (state != RX_STATE) {
    api->say("txbprx: RX state mismatch; no writes");
    return -7;
  }
  if (api->fd_read(FILTER_REG) != FILTER_BASE) {
    api->say("txbprx: AF baseline mismatch; no writes");
    return -8;
  }
  dc_before = api->fd_read(0x7Eu);
  api->say("txbprx begin RX; only r2b");
  if (api->fd_write(FILTER_REG, FILTER_TEST) != 0) {
    api->say("txbprx: filter set refused");
    return -9;
  }
  wrote = 1;
  after = api->fd_read(FILTER_REG);
  api->delay_us(2000u);
  if (api->fd_write(FILTER_REG, FILTER_BASE) == 0)
    restored = 1;
  final = api->fd_read(FILTER_REG);
  dc_after = api->fd_read(0x7Eu);
  if (!wrote || !restored || final != FILTER_BASE ||
      api->fd_read(0x30u) != RX_STATE || dc_before != dc_after) {
    api->say("txbprx RESTORE ERROR; inspect radio");
    return -10;
  }
  if (after != FILTER_TEST) {
    api->say("txbprx: set did not read back; restored");
    return -11;
  }
  api->say("txbprx r2b 0300>0307>0300 OK");
  api->say("txbprx r30 unchanged r7e unchanged");
  return 1;
}
