/* Flux APC baseline fingerprint: no raw PA writes; bounded RF lab only.
 *
 * TX-on uses firmware-calibrated LOW power; read REG_36 when PA
 * enabled and after firmware-owned tx_pa(0). No REG_36 writes are
 * permitted through the module API. User-confirmed dummy load only.
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#ifndef APC_FINGERPRINT_ENABLE
#define APC_FINGERPRINT_ENABLE 0
#endif
#ifndef APC_FIXTURE_ACK
#define APC_FIXTURE_ACK 0
#endif
#ifndef APC_WATCHDOG_ACK
#define APC_WATCHDOG_ACK 0
#endif
#if APC_FINGERPRINT_ENABLE && (!APC_FIXTURE_ACK || !APC_WATCHDOG_ACK)
#error "APC fingerprint needs verified load and independent TX watchdog"
#endif

#define APC_HZ 433500000u
#define REG_ID 0x00u
#define REG_PA 0x36u
#define REG_STATE 0x30u
#define REG_RSSI 0x67u
#if APC_FINGERPRINT_ENABLE
static void hex4(char *p,unsigned v){
  static const char h[]="0123456789ABCDEF";
  unsigned i;
  for(i=0;i<4;i++)p[i]=h[(v>>(12-i*4))&15u];
}
#endif
int k6_main(const k6_api_t *a){
  if(!a || a->abi!=1 || a->level<3)return -3;
  if(a->fd_read(REG_ID)!=0x4829u)return -6;
#if !APC_FINGERPRINT_ENABLE
  a->say("APC fingerprint inert: guarded load needed");
  return -9;
#else
  uint16_t pa_on=0,pa_off=0;
  char line[]="APC on=0000 off=0000";
  int rc=0,keyed=0;
  if(a->rx_hz()!=APC_HZ || a->key_down() || km_power_switch_off() ||
     (a->fd_read(REG_STATE)&2u))return -2;
  if(KM_RSSI_DBM(a->fd_read(REG_RSSI))>-90)return -7;
  a->delay_us(50000u);
  if(a->key_down() || km_power_switch_off() ||
     KM_RSSI_DBM(a->fd_read(REG_RSSI))>-90)return -7;
  if(a->tx_on(650u)!=0)return -5;
  keyed=1;
  /* Exact first PA read at calibrated low-power carrier. */
  pa_on=a->fd_read(REG_PA);
  if(a->tx_pa(0)!=0){rc=-12;goto done;}
  pa_off=a->fd_read(REG_PA);
  if((pa_on & 0x0080u)!=0x0080u || (pa_off&0x0080u)!=0 ||
     (pa_off&0x00ffu)!=0x0022u){rc=-13;goto done;}
  hex4(line+7,pa_on);
  hex4(line+16,pa_off);
  a->say(line);
  rc=1;
done:
  if(keyed)a->tx_off();
  if(rc!=1)a->say("APC fingerprint aborted, TX released");
  return rc;
#endif
}
