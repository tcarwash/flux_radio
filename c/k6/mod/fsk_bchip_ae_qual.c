/* Flux FSK-09S-H / FD6818B 0x4829 TX FIFO almost-empty IRQ qualifier.
 * Prospective, short, guarded, STATUS ONLY. No mid-TX FIFO writes/refills.
 * Two independent 49-word frames, thresholds 24 and 8 FIFO words.
 * Each frame is 48 known words + sacrificial trailing 0xA55A guard to
 * tolerate the observed intermittent 0x00F8 leading word. Measured IRQs
 * are never trusted to authorize FIFO writes in this experiment.
 * Original signed module/VFO restoration handled by EXTERNAL bench runner.
 * Original RX registers restored by module after the 1900ms TX watchdog.
 * INERT by default, experimental B-chip mask/IRQ semantics UNPROVEN.
 */
#ifndef FSK_AE_QUAL_ENABLE
#define FSK_AE_QUAL_ENABLE 0
#endif
#ifndef FSK_AE_QUAL_FIXTURE_ACK
#define FSK_AE_QUAL_FIXTURE_ACK 0
#endif
#ifndef FSK_AE_QUAL_WATCHDOG_ACK
#define FSK_AE_QUAL_WATCHDOG_ACK 0
#endif
#if FSK_AE_QUAL_ENABLE && (!FSK_AE_QUAL_FIXTURE_ACK || !FSK_AE_QUAL_WATCHDOG_ACK)
#error "B-chip AE telemetry requires dummy load/isolated lease and watchdog"
#endif
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "fsk_lab_vector.h"
#define FSK_HZ 433500000u
#define FSK_BAUD_CODE 0xc194u
#define FSK_LEASE_MS 1900u
#define FSK_FRAME_TIMEOUT_MS 700u /* bounded two-frame probe, <1900ms TX lease */
#define FSK_TXDONE_MIN_MS 220u /* 49x16 + 160 public sync/preamble bits at 4800 ~=197ms */
#define FSK_WORDS 49u
#define R_CHIP 0x00u
#define R_WORK 0x3fu
#define R_CTRL 0x59u
#define R_PREAMBLE 0x58u
#define R_GAIN 0x70u
#define R_BAUD 0x72u
#define R_SYNC0 0x5au
#define R_SYNC1 0x5bu
#define R_SYNC2 0x5cu
#define R_LENGTH 0x5du
#define R_FIFO 0x5fu
#define R_FIFO_LEVEL 0x5eu
#define R_IRQ 0x02u
#define R_STATUS 0x0cu
#define R_STATE 0x30u
#define R_RSSI 0x67u
#define IRQ_AE_BIT 0x4000u
#define IRQ_AE_EVENT 0x000eu
#define IRQ_TX_DONE 0x8000u

#if FSK_AE_QUAL_ENABLE
static const uint8_t saved_regs[]={
    R_WORK,R_CTRL,R_PREAMBLE,R_GAIN,R_BAUD,R_SYNC0,R_SYNC1,
    R_SYNC2,R_LENGTH,R_FIFO_LEVEL
};
#define N_SAVED (sizeof(saved_regs)/sizeof(saved_regs[0]))
struct ae_trace {
    uint16_t first_bit14_ms,first_code14_ms,first_done_ms;
    uint16_t irq_or,irq_last,status_or,polls,configured_threshold;
};
static int wr(const k6_api_t *a,uint8_t r,uint16_t v){return a->fd_write(r,v)==0?0:-1;}
static int safe(const k6_api_t *a){return !a->key_down()&&!km_power_switch_off();}
static int free_channel(const k6_api_t *a){return KM_RSSI_DBM(a->fd_read(R_RSSI))<=-90;}
static void puthex(char *p,uint16_t v){
    static const char hd[]="0123456789ABCDEF";
    unsigned i;for(i=0;i<4;i++)p[i]=hd[(v>>(12u-4u*i))&15u];
}
static void report(const k6_api_t *a,unsigned k,const struct ae_trace *t){
    char one[]="AE0 th=0000 bit=0000 evt=0000";
    char two[]="AE0 done=0000 irq=0000 last=0000";
    char three[]="AE0 polls=0000 status=0000";
    one[2]=(char)('0'+k);two[2]=(char)('0'+k);three[2]=(char)('0'+k);
    puthex(one+7,t->configured_threshold);
    puthex(one+16,t->first_bit14_ms);
    puthex(one+25,t->first_code14_ms);
    puthex(two+9,t->first_done_ms);
    puthex(two+18,t->irq_or);
    puthex(two+28,t->irq_last);
    puthex(three+10,t->polls);
    puthex(three+22,t->status_or);
    a->say(one);a->say(two);a->say(three);
}
/* A threshold-only qualifier. A true FIFO empty IRQ is never a refill grant. */
static int send_one(const k6_api_t *a,unsigned which,
                    uint16_t original_5e,struct ae_trace *t){
    unsigned i;
    uint32_t start;
    const uint16_t threshold=which?8u:24u;
    const uint16_t threshold_reg=(original_5e&~(uint16_t)0x03f8u)|(threshold<<3);
#define W(r,v) do{if(wr(a,(r),(uint16_t)(v))!=0)return -13;}while(0)
    t->configured_threshold=threshold;
    t->first_bit14_ms=0xffffu;
    t->first_code14_ms=0xffffu;
    t->first_done_ms=0xffffu;
    t->irq_or=t->irq_last=t->status_or=t->polls=0;
    W(R_CTRL,0x00f8u);
    W(R_IRQ,0u);
    W(R_WORK,IRQ_TX_DONE|IRQ_AE_BIT);  /* explicit TX done + AE sources */
    W(R_PREAMBLE,0x00e1u);
    W(R_GAIN,0x0080u|21u);
    W(R_BAUD,FSK_BAUD_CODE);
    W(R_SYNC0,0x076bu);
    W(R_SYNC1,0x3e36u);
    W(R_SYNC2,0x5625u);
    W(R_LENGTH,0x6100u); /* 98 bytes = 49 FIFO words */
    W(R_FIFO_LEVEL,threshold_reg);
    if(a->fd_read(R_FIFO_LEVEL)!=threshold_reg)return -17;
    W(R_CTRL,0x80f8u);
    W(R_CTRL,0x00f8u);
    for(i=0;i<FSK_WORDS;i++){
        if(!safe(a))return -4;
        W(R_FIFO,i==48u?0xa55au:fsk_lab_words[i]);
    }
    W(R_IRQ,0u);
    W(R_CTRL,0x08f8u);
    start=a->ms();
#undef W
    for(;;){
        uint16_t irq,status,dt;
        if(!safe(a))return -4;
        dt=(uint16_t)(a->ms()-start);
        if(dt>FSK_FRAME_TIMEOUT_MS)return -14;
        /* BK4819 upstream uses write-zero before read to latch IRQ flags.
         * Both bit14 and low-nibble event14 captured; neither assumed B. */
        if(wr(a,R_IRQ,0u)!=0)return -13;
        irq=a->fd_read(R_IRQ);
        status=a->fd_read(R_STATUS);
        t->irq_or|=irq;t->irq_last=irq;t->status_or|=status;
        if(t->polls!=0xffffu)t->polls++;
        if((irq&IRQ_AE_BIT)&&t->first_bit14_ms==0xffffu)t->first_bit14_ms=dt;
        if((irq&15u)==IRQ_AE_EVENT&&t->first_code14_ms==0xffffu)t->first_code14_ms=dt;
        /* On the real B chip 0x8000 appeared in the IRQ latch while
         * REG_0C[0] never asserted. Accept it only after a conservative
         * complete RF frame airtime (784 payload + 160 preamble/sync bits
         * at 4800 baud ~197ms), and still require the independent blind
         * SDR scorer to see both exact frames. Never infer AE headroom. */
        if(dt>=FSK_TXDONE_MIN_MS && ((irq&IRQ_TX_DONE)||(status&1u))){
            t->first_done_ms=dt;
            return (int)FSK_WORDS;
        }
        a->delay_us(1000u);
    }
}
#endif
int k6_main(const k6_api_t *api){
    if(!api||api->abi!=1||api->level<K6_MODULE_LEVEL)return -3;
    if(api->fd_read(R_CHIP)!=0x4829u)return -6;
#if !FSK_AE_QUAL_ENABLE
    api->say("FSK09S H AE qualification inert: no TX");
    return -9;
#else
    uint16_t saved[N_SAVED];
    struct ae_trace trace[2];
    unsigned i,k,attempted_frames=0;
    int ret=0,keyed=0,restore=0;
    if(api->rx_hz()!=FSK_HZ||(api->fd_read(R_STATE)&2u)||!safe(api))return -2;
    if(!free_channel(api))return -7;
    api->delay_us(50000u);
    if(!safe(api)||!free_channel(api))return -7;
    if(api->fd_read(R_WORK)&0xa000u)return -8;
    if(api->tx_on(FSK_LEASE_MS)!=0)return -5;
    keyed=1;
    if(api->tx_pa(0)!=0){ret=-4;goto cleanup;}
    for(i=0;i<N_SAVED;i++)saved[i]=api->fd_read(saved_regs[i]);
    if((saved[0]&0xa000u)||(saved[1]&0x0800u)){ret=-8;goto cleanup;}
    restore=1;
    if(api->tx_pa(1)!=0){ret=-4;goto cleanup;}
    for(k=0;k<2;k++){
        attempted_frames=k+1u;
        ret=send_one(api,k,saved[N_SAVED-1],&trace[k]);
        if(ret!=(int)FSK_WORDS)goto cleanup;
        if(k==0u)api->delay_us(10000u);
    }
    ret=2*(int)FSK_WORDS;
cleanup:
    if(keyed){
        (void)api->tx_pa(0);
        if(restore){
            if(wr(api,R_CTRL,0x00f8u)!=0||
               wr(api,R_WORK,0u)!=0||wr(api,R_IRQ,0u)!=0)ret=-15;
            for(i=N_SAVED;i>0;i--){
                unsigned j=i-1;
                if(wr(api,saved_regs[j],saved[j])!=0)ret=-15;
            }
        }
        api->tx_off();
    }
    /* Publish raw threshold/IRQ/status observations only AFTER de-key and
     * register restoration; a timed-out first frame is valuable evidence.
     * Do not assume AE IRQ encodings or report an uninitialised second trace. */
    if(restore && ret!=-15){
        for(k=0;k<attempted_frames;k++)report(api,k,&trace[k]);
    }
    if(ret!=2*(int)FSK_WORDS)api->say("FSK09S H aborted. RX restored.");
    return ret;
#endif
}
