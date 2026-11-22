/* FD6818B guarded 128-word native-FSK FIFO refill *hypothesis*.
 * Experimental B-chip TX almost-empty IRQ probe. REG_5E and REG_3F
 * threshold/IRQ semantics come from the older FD6818/BK4819 family:
 * they are NOT established for FD6818B. This source is INERT by default.
 *
 * Conducted, user-confirmed dummy-load setup ONLY. Hard TX lease and
 * independent watchdog are required. One 128-word frame, max 3 writes
 * into the active FIFO, no rekey, no retries, no flash/calibration.
 */
#ifndef FSK_REFILL_ENABLE
#define FSK_REFILL_ENABLE 0
#endif
#ifndef FSK_REFILL_FIXTURE_ACK
#define FSK_REFILL_FIXTURE_ACK 0
#endif
#ifndef FSK_REFILL_WATCHDOG_ACK
#define FSK_REFILL_WATCHDOG_ACK 0
#endif
#if FSK_REFILL_ENABLE && (!FSK_REFILL_FIXTURE_ACK || !FSK_REFILL_WATCHDOG_ACK)
#error "FSK refill requires explicit dummy load and independent TX watchdog"
#endif
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "fsk_lab_vector.h"

#define FSK_HZ 433500000u
#define FSK_BAUD 4800u
#define FSK_BAUD_WORD 0xc194u
#define FSK_GAIN 21u
#define FSK_TOTAL_WORDS 128u
#define FSK_LEASE_MS 1900u
#define FSK_FINISH_TIMEOUT_MS 900u

#define CHIP 0x00u
#define R_WORK 0x3fu
#define R_CTRL 0x59u
#define R_PREAMBLE 0x58u
#define R_GAIN 0x70u
#define R_BAUD 0x72u
#define R_SYNC0 0x5au
#define R_SYNC1 0x5bu
#define R_SYNC2 0x5cu
#define R_LEN 0x5du
#define R_FIFO 0x5fu
#define R_FIFO_LEVEL 0x5eu
#define R_IRQ 0x02u
#define R_STATUS 0x0cu
#define R_STATE 0x30u
#define R_RSSI 0x67u
#define TX_FIFO_AE 0x4000u

#if FSK_REFILL_ENABLE
/* Read-only interrupt telemetry for the observed FD6818B behavior.
   Published only after TX is OFF; never a substitute for the watchdog. */
struct irq_telemetry {
    uint16_t irq_or,irq_last,status_or,status_last;
};
static void hex4(char *p,uint16_t v){
    static const char hex[]="0123456789ABCDEF";
    unsigned i;
    for(i=0;i<4;i++)p[i]=hex[(v>>(12-4*i))&15u];
}
static void report_irq(const k6_api_t *a,const struct irq_telemetry *t){
    char line[]="IRQ or=0000 last=0000 status or=0000 last=0000";
    hex4(line+7,t->irq_or);
    hex4(line+17,t->irq_last);
    hex4(line+32,t->status_or);
    hex4(line+42,t->status_last);
    a->say(line);
}
static const uint8_t cfg_regs[]={
    R_WORK,R_CTRL,R_PREAMBLE,R_GAIN,R_BAUD,R_SYNC0,R_SYNC1,
    R_SYNC2,R_LEN,R_FIFO_LEVEL
};
#define N_CFG (sizeof(cfg_regs)/sizeof(cfg_regs[0]))
static int wr(const k6_api_t *a,uint8_t reg,uint16_t val){
    return a->fd_write(reg,val)==0 ? 0 : -1;
}
static int safe(const k6_api_t *a){
    return !a->key_down() && !km_power_switch_off();
}
static uint16_t val_for(unsigned index){
    if(index<64u){
        if(index>=56u)return 0u;
        return fsk_lab_words[index];
    }
    index-=64u;
    if(index<8u)return 0u;
    if(index<16u)return 0xffffu;
    return (uint16_t)~fsk_lab_words[index];
}
static int append_words(const k6_api_t *a,unsigned first,unsigned count){
    unsigned i;
    for(i=first;i<first+count;i++){
        if(!safe(a))return -4;
        if(wr(a,R_FIFO,val_for(i))!=0)return -13;
    }
    return 0;
}
static int await_ae(const k6_api_t *a,uint32_t start,unsigned at_least_ms,
                    unsigned deadline_ms,struct irq_telemetry *trace){
    for(;;){
        uint32_t delta=(uint32_t)(a->ms()-start);
        if(!safe(a))return -4;
        if(delta>deadline_ms)return -16;
        {
            uint16_t status=a->fd_read(R_STATUS);
            uint16_t irq=a->fd_read(R_IRQ);
            trace->irq_or|=irq; trace->irq_last=irq;
            trace->status_or|=status; trace->status_last=status;
            if(status&1u)return -18; /* Too-early frame finish */
            if(delta>=at_least_ms && (irq&TX_FIFO_AE))return 0;
        }
        a->delay_us(1000u);
    }
}
#endif

int k6_main(const k6_api_t *a){
    if(!a || a->abi!=1 || a->level<K6_MODULE_LEVEL)return -3;
    if(a->fd_read(CHIP)!=0x4829u)return -6;
#if !FSK_REFILL_ENABLE
    a->say("fsk refill inert: bench build only");
    return -9;
#else
    uint16_t saved[N_CFG];
    struct irq_telemetry trace={0,0,0,0};
    uint16_t ae_register;
    uint32_t start;
    unsigned i;
    int keyed=0,restore=0,ret=0;
    if(a->rx_hz()!=FSK_HZ || !safe(a) || (a->fd_read(R_STATE)&2u))
        return -2;
    if(KM_RSSI_DBM(a->fd_read(R_RSSI))>-90)return -7;
    a->delay_us(50000u);
    if(!safe(a) || KM_RSSI_DBM(a->fd_read(R_RSSI))>-90)return -7;
    if(a->fd_read(R_WORK)&0xa000u)return -8;
    if(a->tx_on(FSK_LEASE_MS)!=0)return -5;
    keyed=1;
    if(a->tx_pa(0)!=0){ret=-4;goto cleanup;}
    for(i=0;i<N_CFG;i++)saved[i]=a->fd_read(cfg_regs[i]);
    if((saved[0]&0xa000u)||(saved[1]&0x0800u)){ret=-8;goto cleanup;}
    restore=1;
    /* REG_5E[11:6] as a family-level hypothesis; set threshold 24
       words, preserving all other observed bits. B readback required. */
    ae_register=(saved[N_CFG-1]&~(uint16_t)0x0fc0u)|(24u<<6);
#define W(reg,val) do{if(wr(a,(reg),(uint16_t)(val))!=0){ret=-13;goto cleanup;}}while(0)
    W(R_CTRL,0x00f8u);
    W(R_IRQ,0u);
    W(R_WORK,0xc000u); /* TX-finished and FIFO-almost-empty IRQ sources */
    W(R_PREAMBLE,0x00e1u);
    W(R_GAIN,0x0080u|FSK_GAIN);
    W(R_BAUD,FSK_BAUD_WORD);
    W(R_SYNC0,0x076bu);
    W(R_SYNC1,0x3e36u);
    W(R_SYNC2,0x5625u);
    W(R_LEN,0xff00u);  /* 128 FIFO words (256 bytes), one frame */
    W(R_FIFO_LEVEL,ae_register);
    if(a->fd_read(R_FIFO_LEVEL)!=ae_register){ret=-17;goto cleanup;}
    W(R_CTRL,0x80f8u);
    W(R_CTRL,0x00f8u);
    ret=append_words(a,0,48);
    if(ret<0)goto cleanup;
    if(a->tx_pa(1)!=0){ret=-4;goto cleanup;}
    W(R_CTRL,0x08f8u);
    start=a->ms();

    /* Each refill is gated by BOTH an observed AE indication and
       elapsed time sufficient to guarantee FIFO headroom. 48+32+32+16
       words. No refill after the last 16. */
    ret=await_ae(a,start,65u,230u,&trace);
    if(ret<0)goto cleanup;
    ret=append_words(a,48,32);
    if(ret<0)goto cleanup;
    W(R_IRQ,0u);
    ret=await_ae(a,start,170u,350u,&trace);
    if(ret<0)goto cleanup;
    ret=append_words(a,80,32);
    if(ret<0)goto cleanup;
    W(R_IRQ,0u);
    ret=await_ae(a,start,280u,490u,&trace);
    if(ret<0)goto cleanup;
    ret=append_words(a,112,16);
    if(ret<0)goto cleanup;
    W(R_IRQ,0u);
    for(;;){
        if(!safe(a)){ret=-4;break;}
        if(a->fd_read(R_STATUS)&1u){ret=FSK_TOTAL_WORDS;break;}
        if((uint32_t)(a->ms()-start)>FSK_FINISH_TIMEOUT_MS){ret=-14;break;}
        a->delay_us(3000u);
    }
#undef W
cleanup:
    if(keyed){
        (void)a->tx_pa(0);
        if(restore){
            if(wr(a,R_CTRL,0x00f8u)!=0 ||
               wr(a,R_WORK,0u)!=0 ||
               wr(a,R_IRQ,0u)!=0)ret=-15;
            for(i=N_CFG;i>0;i--){
                unsigned j=i-1;
                if(wr(a,cfg_regs[j],saved[j])!=0)ret=-15;
            }
        }
        a->tx_off();
    }
    report_irq(a,&trace);
    if(ret==(int)FSK_TOTAL_WORDS)a->say("fsk refill: 128-word frame completed");
    else a->say("fsk refill: aborted; receiver restored");
    return ret;
#endif
}
