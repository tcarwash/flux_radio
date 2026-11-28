/* End-to-end MIC TIME v2 host: actual compiled C module, deterministic 8-kHz
 * microphone ISR ring, tone bursts + continuous tone + silence and cleanup.
 * No physical IO, RF PA, or UART. SPDX-License-Identifier: MIT */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "../../../c/k6/mod/sotto_mic_raw.c"

static uint32_t clock_ms,next_block,sample_i;
static pcm_ring_t *live_ring;
static uint32_t scratch_mem[256];
static unsigned tx_on_calls,tx_off_calls,mic_on_calls,mic_off_calls,clear_calls,report_calls;
static int failure_case,scenario;
static uint32_t cumulative_ones,cumulative_edges,cumulative_var,previous_density=0xffffffff;
static char captured[64];

static void advance(uint32_t now){
  while(live_ring && next_block+10<=now){
    next_block+=10;
    if((uint8_t)(live_ring->produced-live_ring->consumed)>=4){live_ring->lost=1;return;}
    unsigned slot=(live_ring->produced&3)*80;
    for(unsigned k=0;k<80;k++){
      uint32_t sample=sample_i++;
      unsigned amp=scenario==0?(sample<960?4000:0):scenario==1?4000:1;
      double x=sin(2.*3.141592653589793*(sample&7)/8.);
      live_ring->pcm[slot+k]=(int16_t)lrint((double)amp*x);
      // Synthetic PA11 1-bit density as a controlled 48-oversample slice.
      // During a tone, average density stays ~50%, but per-tick density
      // oscillates by 8 bits; in silence density becomes constant.
      uint32_t ones=24u+(amp>1u?(uint32_t)(8.0*x+8.01)-8u:0u);
      uint32_t changes=amp>1u?(ones/2u):24u;
      cumulative_ones+=ones;
      cumulative_edges+=changes;
      if(previous_density!=0xffffffffu)
          cumulative_var+=ones>previous_density?ones-previous_density:previous_density-ones;
      previous_density=ones;
    }
    live_ring->offset=0;
    live_ring->produced=(uint8_t)(live_ring->produced+1);
  }
}
static uint32_t ms(void){clock_ms++;advance(clock_ms);return clock_ms;}
static void delay(uint32_t us){clock_ms+=(us+999)/1000;advance(clock_ms);}
static int key(void){return 0;}
static void *scratch(uint32_t *n){*n=sizeof(scratch_mem);return scratch_mem;}
static uint16_t fd_read(uint8_t r){return r==0x64?542:0x4829;}
static int tx_on(uint32_t limit){if(limit!=2000)return -1;tx_on_calls++;return 0;}
static void tx_off(void){tx_off_calls++;}
static int stream(uint32_t op,void *ptr,uint32_t n){
  if(op==3)return failure_case?-1:1;
  if(op==2)return 5500;
  if(op==4)return live_ring?(int)cumulative_ones:-1;
  if(op==5)return live_ring?(int)cumulative_edges:-1;
  if(op==6)return live_ring?(int)cumulative_var:-1;
  if(op==0){
    if(!tx_on_calls||n!=644||!ptr||live_ring)return -1;
    live_ring=(pcm_ring_t *)ptr;next_block=clock_ms;mic_on_calls++;
    cumulative_ones=0;cumulative_edges=0;cumulative_var=0;previous_density=0xffffffff;
    return 0;
  }
  if(op==1){if(!live_ring)return -1;live_ring=NULL;mic_off_calls++;return 0;}
  return -1;
}
static void lcd_text(int i,const char*s){(void)i;(void)s;}
static int lcd_flush(void){return 0;}
static void say(const char*s){
  if(strcmp(s,"MAB1:CLR")==0){clear_calls++;captured[0]=0;return;}
  if(strncmp(s,"MAB1:",5)==0){
    if(strlen(s)!=49)abort();strcpy(captured,s);report_calls++;
  }
}
int main(int argc,char **argv){
  scenario=argc>1?(strcmp(argv[1],"steady")==0?1:strcmp(argv[1],"quiet")==0?2:0):0;
  failure_case=argc>1 && strcmp(argv[1],"uart")==0;
  k6_api_t api={0};
  api.abi=1;api.level=31;api.say=say;api.ms=ms;api.delay_us=delay;
  api.key_down=key;api.scratch=scratch;api.fd_read=fd_read;
  api.tx_on=tx_on;api.tx_off=tx_off;api.mic_stream=stream;
  api.lcd_text=lcd_text;api.lcd_flush=lcd_flush;
  int ret=k6_main(&api);
  if(failure_case){
    if(ret!=-4||tx_on_calls||mic_on_calls||report_calls||clear_calls!=1)return 10;
    puts("UART exclusion PASS");return 0;
  }
  if(ret!=6||!tx_on_calls||!tx_off_calls||!mic_on_calls||!mic_off_calls||
      live_ring||report_calls!=1||clear_calls!=1||strlen(captured)!=49)return 11;
  printf("encoded=%s\n",captured);
  puts("RAW PA11 CAPTURE + PA RESTORE PASS");return 0;
}
