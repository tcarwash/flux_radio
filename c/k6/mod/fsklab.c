/* Flux FD6818B native-FSK known-vector transmit *laboratory* module.
 *
 * DEFAULT BUILD IS INERT.  An enabled build requires explicit compile-time
 * acknowledgments for a rated, isolated dummy-load RF fixture and an
 * independently verified watchdog.  Never operate with an antenna, and
 * never treat PA-off as RF-off.  The module is not a legal station ID.
 *
 * Only the 0x4829 chip, the guarded level-3 module TX API and the
 * original B-chip native-FSK register sequence are used.
 * REG_40 (FM deviation) is deliberately NOT changed: mapping remains
 * an unverified physical experiment.
 *
 * This does not install firmware or access flash/calibration.
 * SPDX-License-Identifier: MIT
 */
#ifndef FSK_LAB_ENABLE
#define FSK_LAB_ENABLE 0
#endif
#ifndef FSK_LAB_FIXTURE_ACK
#define FSK_LAB_FIXTURE_ACK 0
#endif
#ifndef FSK_LAB_WATCHDOG_ACK
#define FSK_LAB_WATCHDOG_ACK 0
#endif
#if FSK_LAB_ENABLE && (!FSK_LAB_FIXTURE_ACK || !FSK_LAB_WATCHDOG_ACK)
#error "FSK lab TX requires isolated dummy load and independent TX watchdog"
#endif

#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "fsk_lab_vector.h"

#define REG_CHIP_ID 0x00u
#define REG_MODE 0x3fu
#define REG_CTRL 0x59u
#define REG_FREQ_GAIN 0x70u
#define REG_BAUD 0x72u
#define REG_PREAMBLE 0x58u
#define REG_SYNC0 0x5au
#define REG_SYNC1 0x5bu
#define REG_SYNC2 0x5cu
#define REG_LENGTH 0x5du
#define REG_FIFO 0x5fu
#define REG_IRQ_CLEAR 0x02u
#define REG_STATUS 0x0cu
#define REG_STATE 0x30u
#define REG_RSSI 0x67u

#define FSK_LAB_TX_LIMIT_MS 2600u
#define FSK_LAB_COMPLETION_TIMEOUT_MS 1350u
#define FSK_LAB_GAIN_LOW 16u
#define FSK_LAB_GAIN_HIGH 96u

#if FSK_LAB_ENABLE
/* Eight registers and MODE/CTRL, excluding destructive FIFO readback.
 * Ordered restoration happens with PA disabled and with TX still guarded. */
static const uint8_t cfg_regs[] = {
  REG_MODE, REG_CTRL, REG_PREAMBLE, REG_FREQ_GAIN, REG_BAUD,
  REG_SYNC0, REG_SYNC1, REG_SYNC2, REG_LENGTH
};
#define CONFIG_REGISTER_COUNT (sizeof(cfg_regs) / sizeof(cfg_regs[0]))

static int write_or_fail(const k6_api_t *api, uint8_t reg, uint16_t value) {
  return api->fd_write(reg,value) == 0 ? 0 : -1;
}

static int still_ok(const k6_api_t *api) {
  return !api->key_down() && !km_power_switch_off();
}

static int channel_free(const k6_api_t *api) {
  return KM_RSSI_DBM(api->fd_read(REG_RSSI)) <= -90;
}
#endif

int k6_main(const k6_api_t *api) {
  if (!api || api->abi != 1 || api->level < K6_MODULE_LEVEL) return -3;
  if (api->fd_read(REG_CHIP_ID) != 0x4829u) return -6;
#if !FSK_LAB_ENABLE
  api->say("fsklab inert: fixture+watchdog required");
  return -9;
#else
  unsigned i;
  int ret=0, keyed=0, attempted_restore=0;
  uint16_t saved[CONFIG_REGISTER_COUNT];
  uint32_t started;

  if (FSK_LAB_VECTOR_WORDS < 32u || FSK_LAB_VECTOR_WORDS > 64u ||
      FSK_LAB_GAIN < FSK_LAB_GAIN_LOW || FSK_LAB_GAIN > FSK_LAB_GAIN_HIGH ||
      (FSK_LAB_BAUD != 1200u && FSK_LAB_BAUD != 2400u &&
       FSK_LAB_BAUD != 3600u && FSK_LAB_BAUD != 4800u) ||
      FSK_LAB_FREQUENCY_HZ != 433500000u ||
      FSK_LAB_VECTOR_BITS != FSK_LAB_VECTOR_WORDS*16u)
    return -12;

  if (api->rx_hz() != FSK_LAB_FREQUENCY_HZ ||
      !still_ok(api) || api->fd_read(REG_STATE) & 2u)
    return -2;
  /* This is an occupancy precaution, not a substitute for a conducted
     fixture.  The bench source does not attempt CSMA or unattended TX. */
  if (!channel_free(api)) return -7;
  api->delay_us(50000u);
  if (!still_ok(api) || !channel_free(api)) return -7;
  if (api->fd_read(REG_MODE) & 0xA000u) return -8;

  if (api->tx_on(FSK_LAB_TX_LIMIT_MS) != 0) return -5;
  keyed=1;  /* Any error from this point goes through explicit TX teardown. */
  if (api->tx_pa(0) != 0) {ret=-4;goto done;}

  for(i=0;i<CONFIG_REGISTER_COUNT;i++)
    saved[i]=api->fd_read(cfg_regs[i]);
  /* Guard against another modem still configured; never share the FIFO. */
  if ((saved[0] & 0xA000u) || (saved[1] & 0x0800u)) {
    ret=-8;goto done;
  }
  attempted_restore=1;
  /* This is the proven B-chip lab sequence.  The modem's own preamble and
     sync settings are distinct from the explicit known payload header. */
#define WR(reg,value) do { \
    if(write_or_fail(api,(reg),(uint16_t)(value)) != 0) {ret=-13;goto done;} \
  } while (0)
  WR(REG_CTRL,0x00f8u);
  WR(REG_IRQ_CLEAR,0u);
  WR(REG_MODE,0x8000u);
  WR(REG_PREAMBLE,0x00e1u);
  WR(REG_FREQ_GAIN,0x0080u | FSK_LAB_GAIN);
  WR(REG_BAUD,FSK_LAB_BAUD_WORD);
  WR(REG_SYNC0,0x076bu);
  WR(REG_SYNC1,0x3e36u);
  WR(REG_SYNC2,0x5625u);
  WR(REG_LENGTH,((FSK_LAB_VECTOR_WORDS*2u-1u)<<8));
  WR(REG_CTRL,0x80f8u);  /* FIFO clear */
  WR(REG_CTRL,0x00f8u);
  for(i=0;i<FSK_LAB_VECTOR_WORDS;i++) {
    if (!still_ok(api)) {ret=-4;goto done;}
    WR(REG_FIFO,fsk_lab_words[i]);
  }
  if (api->tx_pa(1)!=0) {ret=-4;goto done;}
  WR(REG_CTRL,0x08f8u);  /* FSK TX enable */
#undef WR
  started=api->ms();
  for (;;) {
    if (!still_ok(api)) {ret=-4;break;}
    if (api->fd_read(REG_STATUS) & 1u) {ret=(int)FSK_LAB_VECTOR_WORDS;break;}
    if ((uint32_t)(api->ms()-started)>FSK_LAB_COMPLETION_TIMEOUT_MS) {
      ret=-14;break;
    }
    api->delay_us(5000u);
  }
done:
  if(keyed) {
    /* Hardware PA is disabled BEFORE any mode/control changes.
       Even PA=0 can leak RF: conducted dummy load remains essential. */
    (void)api->tx_pa(0);
    if(attempted_restore) {
      /* Stop modem, clear pending event, and restore the exact snapshot.
         All calls are guarded, and completion/refusal is reported. */
      if(write_or_fail(api,REG_CTRL,0x00f8u)!=0 ||
         write_or_fail(api,REG_MODE,0u)!=0 ||
         write_or_fail(api,REG_IRQ_CLEAR,0u)!=0)
        ret=-15;
      for(i=CONFIG_REGISTER_COUNT;i>0;i--) {
        unsigned j=i-1;
        if(write_or_fail(api,cfg_regs[j],saved[j])!=0) ret=-15;
      }
    }
    api->tx_off();
  }
  if(ret==(int)FSK_LAB_VECTOR_WORDS)
    api->say("fsklab known frame sent; RX restored");
  else
    api->say("fsklab aborted; verify RX, no retry");
  return ret;
#endif
}
