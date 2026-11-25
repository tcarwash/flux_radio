/* Host integration fixture exercises the exact MIC SWEEP module entry with
 * a deterministic mocked 8-kHz microphone ring and guarded radio callbacks.
 * No RF equipment or UART is used. SPDX-License-Identifier: MIT */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "../../../c/k6/mod/sotto_mic_ab.c"

static uint32_t clock_ms, next_block, gen_i;
static pcm_ring_t *live_ring;
static uint32_t mem[256];
static uint16_t reg7d=0xe93f;
static unsigned gain_count, tx_start_count, tx_stop_count, mic_start_count, mic_stop_count;
static uint8_t gain_history[2];
static int deny_gain;
static char lcd[6][24], reported[3][24];
static int reported_valid;
static const unsigned amplitudes[32]={[0]=100,[8]=800,[16]=1900,[24]=3200,[31]=4600};

static void advance(uint32_t t){
    while(live_ring && next_block+10<=t){
        next_block+=10;
        if((uint8_t)(live_ring->produced-live_ring->consumed)>=4u){live_ring->lost=1;return;}
        unsigned slot=(live_ring->produced&3u)*80u;
        for(unsigned j=0;j<80;j++){
            double angle=2.*3.141592653589793*(gen_i&7u)/8.;
            live_ring->pcm[slot+j]=(int16_t)lrint(sin(angle)*(double)amplitudes[reg7d&31u]);
            gen_i++;
        }
        live_ring->offset=0;
        live_ring->produced=(uint8_t)(live_ring->produced+1u);
    }
}
static uint32_t ms(void){clock_ms++;advance(clock_ms);return clock_ms;}
static void delay(uint32_t us){clock_ms+=(us+999u)/1000u;advance(clock_ms);}
static int key(void){return 0;}
static void *scratch(uint32_t *n){*n=sizeof(mem);return mem;}
static uint16_t read_reg(uint8_t reg){return reg==0x7d?reg7d:reg==0x64?(uint16_t)(100+(reg7d&31u)):0x4829;}
static int write_reg(uint8_t reg,uint16_t v){
    if(reg!=0x7d||deny_gain)return -1;
    if(gain_count<2)gain_history[gain_count]=(uint8_t)(v&31u);
    gain_count++;
    reg7d=v;
    return 0;
}
static int ptt(uint32_t op,void *ptr,uint32_t n){
    if(op==3)return 1;
    if(op==2)return 4304;
    if(op==0){
        if(!tx_start_count||n!=644||!ptr||live_ring)return -1;
        mic_start_count++;
        reg7d=0xe93f;
        live_ring=(pcm_ring_t *)ptr;
        next_block=clock_ms;
        return 0;
    }
    if(op==1){mic_stop_count++;live_ring=0;reg7d=0xe920;return 0;}
    return -1;
}
static int tx_on(uint32_t max_ms){if(max_ms!=3500)return -1;tx_start_count++;return 0;}
static void tx_off(void){tx_stop_count++;}
static void lcd_text(int row,const char *s){if(row>=0&&row<6)snprintf(lcd[row],sizeof(lcd[row]),"%s",s);}
static int lcd_flush(void){
    if(!reported_valid&&strcmp(lcd[0],"STAGE 1K CLIP")==0){
        for(int i=0;i<3;i++)strcpy(reported[i],lcd[i+1]);
        reported_valid=1;
    }
    return 0;
}
int main(int argc,char **argv){
    deny_gain=(argc>1&&strcmp(argv[1],"deny")==0);
    k6_api_t api={0};
    api.abi=1;api.level=31;api.fd_read=read_reg;api.fd_write=write_reg;
    api.delay_us=delay;api.ms=ms;api.key_down=key;api.scratch=scratch;
    api.tx_on=tx_on;api.tx_off=tx_off;api.mic_stream=ptt;
    api.lcd_text=lcd_text;api.lcd_flush=lcd_flush;
    int ret=k6_main(&api);
    if(deny_gain){
        if(ret!=-7||!tx_start_count||!tx_stop_count||!mic_start_count||!mic_stop_count||live_ring)
            return 11;
        puts("GAIN REJECTION CLEANUP PASS");
        return 0;
    }
    if(ret!=3||!reported_valid||gain_count!=2||tx_start_count!=1||tx_stop_count!=1||
       mic_start_count!=1||mic_stop_count!=1||live_ring)return 12;
    const uint8_t expected[3]={31,31,8};
    unsigned tone_values[3]={0};
    for(unsigned i=0;i<3;i++){
        unsigned tone=0,clip=0;
        const char *stage=i==0?"B31":i==1?"W31":"W08";
        char expected_line[12];
        snprintf(expected_line,sizeof(expected_line),"%s 1K=",stage);
        if(strncmp(reported[i],expected_line,strlen(expected_line))!=0)return 13;
        if(sscanf(reported[i]+strlen(expected_line),"%u C=%u",&tone,&clip)!=2)return 14;
        if(tone<200||clip!=0)return 15;
        tone_values[i]=tone;
        printf("stage=%s 1K=%u CLIP=%u\n",stage,tone,clip);
    }
    // Neither the initial baseline nor its matching re-write should shift tone.
    if(abs((int)tone_values[0]-(int)tone_values[1])>10||tone_values[2]>=tone_values[1])return 16;
    if(gain_history[0]!=31||gain_history[1]!=8||reg7d!=0xe920)return 17;
    puts("SWEEP + CLEANUP PASS");
    return 0;
}
