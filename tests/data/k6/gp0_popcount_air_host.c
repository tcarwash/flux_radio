/* Synthetic host for EXACT experimental GP0 raw PDM group popcount RF module.
 * No SDR, no radio, no serial, no network. Sensor + FSK callbacks mocked.
 * SPDX-License-Identifier: MIT */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "k6_mod.h"
#include "sotto_live_engine.h"

extern int k6_main(const k6_api_t *);

static uint32_t elapsed_us, capture_begin_us;
static sl_pcm_t *ring_ptr;
static unsigned packet_count;
static int lease,route,active,fsk_active,fails,fail_step;
static uint8_t wire[86][66];
static uint32_t scratch_mem[12000/4];
static uint32_t fsk_begin_us;

static void pump(void){
    if(!active||fail_step==2||ring_ptr==NULL||ring_ptr->produced)return;
    if(elapsed_us-capture_begin_us>=44000u){
        uint8_t *out=(uint8_t*)ring_ptr+sizeof(sl_pcm_t);
        for(unsigned i=0;i<4096u;i++)out[i]=(uint8_t)(i%65u);
        ring_ptr->produced=1;
    }
}
static uint32_t mock_ms(void){pump();return elapsed_us/1000u;}
static uint32_t mock_ticks(void){return elapsed_us/100u;}
static void mock_delay(uint32_t us){elapsed_us+=us;pump();}
static void mock_say(const char *s){(void)s;}
static uint16_t mock_fd_read(uint8_t reg){return reg==0?0x4829u:0;}
static uint32_t mock_rx_hz(void){return fail_step==3?433500000u:443500000u;}
static int mock_key(void){return 0;}
static int mock_mic(uint32_t op,void *p,uint32_t n){
    if(op==3)return fail_step==4?-1:((fail_step==8&&packet_count>=4)?0:1);
    if(op==7){if(fail_step==5)return -1;route=1;return 0;}
    if(op==9){
        if(!route||n!=4096u||!p)return -1;
        ring_ptr=(sl_pcm_t*)p;capture_begin_us=elapsed_us;active=1;
        return 0;
    }
    if(op==1){active=0;route=0;return fail_step==7?-1:0;}
    return -1;
}
static int mock_tx_on(uint32_t ms){if(ms!=25000u||lease)return -1;lease=1;return 0;}
static void mock_tx_off(void){lease=0;active=0;route=0;fsk_active=0;}
static int mock_fsk(uint32_t op,const uint16_t*w,uint32_t n){
    if(op==2){fsk_active=0;return 0;}
    if(op==0){
        if(!lease||active||route||fsk_active||!w||n!=33u||packet_count>=86u)
            return -1;
        if(w[32]!=0xa55au)return -1;
        memcpy(wire[packet_count],w,66);
        packet_count++;fsk_active=1;fsk_begin_us=elapsed_us;
        return 0;
    }
    if(op==1){
        if(!fsk_active)return -1;
        if(fail_step==6&&packet_count==5)return -1;
        if(elapsed_us-fsk_begin_us>=145000u){fsk_active=0;return 1;}
        return 0;
    }
    return -1;
}
static void mock_lcd_text(int row,const char*txt){(void)row;(void)txt;}
static int mock_lcd_flush(void){return 0;}
static void *mock_scratch(uint32_t*len){
    *len=sizeof(scratch_mem);
    return (uint8_t*)scratch_mem;
}

int gpraw_host_run(int fail_mode){
    k6_api_t a;
    memset(&a,0,sizeof(a));
    memset(wire,0,sizeof(wire));
    memset(scratch_mem,0,sizeof(scratch_mem));
    elapsed_us=capture_begin_us=0;ring_ptr=NULL;packet_count=0;
    lease=route=active=fsk_active=fails=0;fail_step=fail_mode;
    a.abi=1;a.level=30;a.ms=mock_ms;a.ticks=mock_ticks;
    a.delay_us=mock_delay;a.fd_read=mock_fd_read;
    a.rx_hz=mock_rx_hz;a.key_down=mock_key;
    a.mic_stream=mock_mic;a.tx_on=mock_tx_on;a.tx_off=mock_tx_off;
    a.fsk_async=mock_fsk;a.scratch=mock_scratch;
    a.lcd_text=mock_lcd_text;a.lcd_flush=mock_lcd_flush;
    a.say=mock_say;
    int result=k6_main(&a);
    if(lease||route||active||fsk_active)fails++;
    return result;
}
int gpraw_host_faults(void){return fails;}
unsigned gpraw_host_packet_count(void){return packet_count;}
uint32_t gpraw_host_elapsed_us(void){return elapsed_us;}
const uint8_t *gpraw_host_packet(unsigned i){return i<packet_count?wire[i]:NULL;}
