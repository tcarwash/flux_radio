/* Host-only behavioral checks against the actual K6 CW KEY source.
 * Never touches real serial, radio, GPIO, PA, PTT, SPI or RF hardware. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>

#ifndef CW_TEXT
#define CW_TEXT "E"
#endif
#define CW_WAIT_MS 3000u
#define CW_CLEAR_MS 100u
#define CW_LEAD_MS 300u
#include "../../c/k6/mod/cwkey.c"

struct mock {
    uint64_t us;
    uint64_t cancel_at_us;
    uint64_t tx_start_us;
    int tx_on_count, tx_off_count, tx_pa_on_count, tx_pa_off_count;
    int pa, keyed, off_failed;
    int fail_on, fail_off, fail_tx_on;
    int unsafe_post_cancel_on, delay_crossed_cancel_us;
    uint32_t latest_delay_us;
    uint32_t ms_epoch;
    uint16_t rssi_word;
    int ms_before_initial_pa_off;
    char history[2048];
    unsigned n_history;
};
static struct mock m;

static void log_event(char c){
    if (m.n_history<sizeof(m.history)-1) m.history[m.n_history++]=c;
    m.history[m.n_history]=0;
}
static void say_cb(const char *s){(void)s;}
static uint32_t ms_cb(void){
    if (m.keyed && m.pa && m.tx_pa_off_count==0)m.ms_before_initial_pa_off++;
    return m.ms_epoch+(uint32_t)(m.us/1000u);
}
static uint32_t rx_hz_cb(void){return CW_HZ;}
static uint16_t read_cb(uint8_t reg){(void)reg;return m.rssi_word;}
static int key_cb(void){return m.us>=m.cancel_at_us;}
static void delay_cb(uint32_t micros){
    if (m.us<m.cancel_at_us && m.us+(uint64_t)micros>=m.cancel_at_us && m.latest_delay_us==0)
        m.delay_crossed_cancel_us=(int)micros;
    m.latest_delay_us=micros;
    m.us+=micros;
}
static int tx_on_cb(uint32_t limit_ms){
    (void)limit_ms;
    m.tx_on_count++;
    log_event('T');
    if (m.fail_tx_on)return -1;
    m.keyed=1;m.pa=1; /* Model tx_on initially asserting PA */
    m.tx_start_us=m.us;
    return 0;
}
static void tx_off_cb(void){
    m.tx_off_count++;
    m.keyed=m.pa=0;
    log_event('X');
}
static int pa_cb(int on){
    if (!m.keyed)return -1;
    if (on){
        m.tx_pa_on_count++;
        log_event('1');
        if (m.us>=m.cancel_at_us)m.unsafe_post_cancel_on++;
        if (m.fail_on)return -1;
        m.pa=1;
    } else {
        m.tx_pa_off_count++;
        log_event('0');
        if (m.fail_off){m.off_failed++;return -1;}
        m.pa=0;
    }
    return 0;
}

static int run_custom(uint64_t cancel_at_us,int fail_on,int fail_off,int fail_tx_on,
                      uint32_t ms_epoch,uint16_t rssi_word){
    memset(&m,0,sizeof(m));
    m.cancel_at_us=cancel_at_us;
    m.ms_epoch=ms_epoch;
    m.rssi_word=rssi_word;
    m.fail_on=fail_on;
    m.fail_off=fail_off;
    m.fail_tx_on=fail_tx_on;
    k6_api_t api={0};
    api.abi=1;
    api.level=3;
    api.say=say_cb;
    api.ms=ms_cb;
    api.rx_hz=rx_hz_cb;
    api.fd_read=read_cb;
    api.delay_us=delay_cb;
    api.key_down=key_cb;
    api.tx_on=tx_on_cb;
    api.tx_off=tx_off_cb;
    api.tx_pa=pa_cb;
    ui_t u={0};
    return tx_main(&api,&u);
}
static int run(uint64_t cancel_at_us,int fail_on,int fail_off,int fail_tx_on){
    return run_custom(cancel_at_us,fail_on,fail_off,fail_tx_on,0,0);
}
#define CHECK(cond) do {if(!(cond)) {fprintf(stderr,"FAIL line %d: %s history=%s t=%llu us\n",__LINE__,#cond,m.history,(unsigned long long)m.us); return 1;}}while(0)
int main(void){
#ifdef CW_HARNESS_LONG_TEXT
    int long_r=run(UINT64_MAX,0,0,0);
    CHECK(long_r==-6);
    CHECK(m.tx_on_count==0 && m.tx_pa_on_count==0 && m.tx_off_count==0);
    printf("LONG_TEXT_REJECTED\n");
    return 0;
#endif
    /* Successful one-element E, one PA on, key-off restoration. */
    int r=run(UINT64_MAX,0,0,0);
    CHECK(r>0);
    CHECK(m.tx_on_count==1 && m.tx_off_count==1 && m.tx_pa_on_count==1);
    CHECK(m.pa==0 && m.keyed==0 && m.unsafe_post_cancel_on==0);
    CHECK(m.ms_before_initial_pa_off==0); /* immediate PA off before timestamp */
    CHECK(strncmp(m.history,"T0",2)==0);
    printf("CLEAN pa_calls=%d off_calls=%d tx_off=%d\n",m.tx_pa_on_count,m.tx_pa_off_count,m.tx_off_count);

    /* A hardware user key press during PLL lead-in must stop within 10ms
     * (not let an uninterruptible 300ms delay expire). */
    r=run((CW_CLEAR_MS+100u)*1000u,0,0,0);
    CHECK(r==-4);
    CHECK(m.tx_on_count==1 && m.tx_off_count==1);
    CHECK(m.pa==0 && m.keyed==0);
    CHECK(m.us<=((CW_CLEAR_MS+100u)+20u)*1000u);
    CHECK(m.unsafe_post_cancel_on==0);
    printf("LEAD_CANCEL t_us=%llu\n",(unsigned long long)m.us);

    /* Cancel just before the first Morse mark: no PA enable after key. */
    r=run((CW_CLEAR_MS+CW_LEAD_MS)*1000u,0,0,0);
    CHECK(r==-4);
    CHECK(m.unsafe_post_cancel_on==0);
    CHECK(m.tx_off_count==1 && m.pa==0);
    printf("MARK_CANCEL unsafe_on=%d\n",m.unsafe_post_cancel_on);
    /* Cancel during a keyed mark: the PA must be turned off promptly, not
     held through the rest of a full 60-ms dot. */
    r=run((CW_CLEAR_MS+CW_LEAD_MS+25u)*1000u,0,0,0);
    CHECK(r==-4);
    CHECK(m.tx_pa_on_count==1 && m.tx_off_count==1);
    CHECK(m.us<=((CW_CLEAR_MS+CW_LEAD_MS+25u)+20u)*1000u);
    CHECK(m.pa==0 && m.keyed==0 && m.unsafe_post_cancel_on==0);
    printf("MID_MARK_CANCEL t_us=%llu\n",(unsigned long long)m.us);

    /* Failed first PA ON must always attempt final safe OFF and TX OFF. */
    r=run(UINT64_MAX,1,0,0);
    CHECK(r==-5);
    CHECK(m.tx_off_count==1 && m.pa==0 && m.keyed==0);
    CHECK(m.tx_pa_off_count>=2);
    printf("PA_ON_FAIL off_calls=%d\n",m.tx_pa_off_count);

    /* Failed PA OFF must never be returned as successful even if tx_off is safe. */
    r=run(UINT64_MAX,0,1,0);
    CHECK(r==-5);
    CHECK(m.tx_off_count==1 && m.pa==0 && m.keyed==0);
    printf("PA_OFF_FAIL off_failed=%d\n",m.off_failed);

    r=run(UINT64_MAX,0,0,1);
    CHECK(r==-3);
    CHECK(m.tx_pa_on_count==0 && m.keyed==0);
    printf("TX_ON_REFUSED\n");

    /* A 32-bit wrapped, monotonic module timestamp is not 'busy forever'. */
    r=run_custom(UINT64_MAX,0,0,0,UINT32_MAX-50u,0);
    CHECK(r>0 && m.tx_on_count==1 && m.tx_off_count==1);
    CHECK(m.pa==0 && m.keyed==0);
    printf("CLOCK_WRAP_CLEAN\n");

    /* Feed permanent strong RSSI. The waiting guard must time out without
       ever attempting transmitter enable, including across ms wrap. */
    r=run_custom(UINT64_MAX,0,0,0,UINT32_MAX-50u,188);
    CHECK(r==-7 && m.tx_on_count==0 && m.tx_off_count==0);
    CHECK(m.pa==0 && m.keyed==0 && m.us>=CW_WAIT_MS*1000ull);
    printf("BUSY_TIMEOUT_CLEAN t_us=%llu\n",(unsigned long long)m.us);
    return 0;
}
