/* Flux APC envelope staircase TX, B-chip 0x4829, API level 27.
 *
 * No raw REG_36 write from module! Firmware-owned apc_set() only:
 * percent = 100,90,80,70,80,90,100, each 100ms.
 * Applied to live calibrated LOW-power target, no increase.
 * Inert unless explicit fixture+watchdog compile flags.
 * Conducted dummy-load experiment only; no station ID.
 */
#define K6_MODULE_LEVEL 27
#include "../k6_mod.h"
#ifndef APC_STEP_ENABLE
#define APC_STEP_ENABLE 0
#endif
#ifndef APC_STEP_FIXTURE_ACK
#define APC_STEP_FIXTURE_ACK 0
#endif
#ifndef APC_STEP_WATCHDOG_ACK
#define APC_STEP_WATCHDOG_ACK 0
#endif
#if APC_STEP_ENABLE && (!APC_STEP_FIXTURE_ACK || !APC_STEP_WATCHDOG_ACK)
#error "APC step requires dummy load and independent firmware watchdog"
#endif
#define APC_STEP_HZ 433500000u
#define APC_STEP_TX_LIMIT_MS 1650u
#define APC_STEP_MS 100u
#define APC_STEP_COUNT 7u
#define R_ID 0x00u
#define R_STATE 0x30u
#define R_PA 0x36u
#define R_RSSI 0x67u

#if APC_STEP_ENABLE
static const uint8_t steps[APC_STEP_COUNT]={100,90,80,70,80,90,100};
static int safe(const k6_api_t *a){
    return !a->key_down() && !km_power_switch_off();
}
static void hex4(char *out,uint16_t v){
    static const char tab[]="0123456789ABCDEF";
    unsigned k;
    for(k=0;k<4;k++)out[k]=tab[(v>>(12-4*k))&15u];
}
#endif

int k6_main(const k6_api_t *a){
    if(!a || a->abi!=1 || a->level<27)return -3;
    if(a->fd_read(R_ID)!=0x4829u)return -6;
#if !APC_STEP_ENABLE
    a->say("APC staircase inert; lab enable required");
    return -9;
#else
    uint16_t original=0,observed=0;
    uint32_t begin=0;
    unsigned i;
    int ret=0,keyed=0;
    char message[]="APC base=0000 count=7";
    if(!a->apc_set)return -3;
    if(a->rx_hz()!=APC_STEP_HZ || !safe(a) ||
       (a->fd_read(R_STATE)&2u))return -2;
    if(KM_RSSI_DBM(a->fd_read(R_RSSI))>-90)return -7;
    a->delay_us(50000u);
    if(!safe(a)||KM_RSSI_DBM(a->fd_read(R_RSSI))>-90)return -7;
    if(a->tx_on(APC_STEP_TX_LIMIT_MS)!=0)return -5;
    keyed=1;
    original=a->fd_read(R_PA);
    if((original&0x00ffu)!=0x00a2u || (original>>8)<32u){
        ret=-12;goto done;
    }
    hex4(message+9,original);
    a->say(message);
    for(i=0;i<APC_STEP_COUNT;i++){
        /* Exact floor(n/100) for n<=25550, using a reciprocal.
           Cortex-M0 PIC modules cannot import __aeabi_uidiv. */
        unsigned n=(unsigned)(original>>8)*steps[i]+50u;
        unsigned q=(n*5243u)>>19;
        uint16_t target=(uint16_t)((q<<8)|0x00a2u);
        if(!safe(a)){ret=-4;goto done;}
        if(a->apc_set(steps[i])!=0){ret=-14;goto done;}
        observed=a->fd_read(R_PA);
        if(observed!=target){ret=-15;goto done;}
        begin=a->ms();
        while((uint32_t)(a->ms()-begin)<APC_STEP_MS){
            if(!safe(a)){ret=-4;goto done;}
            a->delay_us(5000u);
        }
    }
    ret=(int)APC_STEP_COUNT;
done:
    if(keyed){
        /* Guaranteed TX-off also restores ordinary RX calibration;
           explicitly restore baseline when callback is still available. */
        (void)a->apc_set(100u);
        a->tx_off();
    }
    if(ret==(int)APC_STEP_COUNT)a->say("APC staircase done; RX restored");
    else a->say("APC staircase aborted; TX released");
    return ret;
#endif
}
