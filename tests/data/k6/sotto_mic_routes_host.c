/* Executes actual MIC ROUTES C module with deterministic fake FD6818, GPIO
 * and 8 kHz ISR ring. No physical radio/serial/RF side effects. MIT. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
volatile uint32_t mic_routes_host_moder=0;
static unsigned gpio_reads=0;
static uint16_t regs[128];
uint32_t mic_routes_host_gp0_read(void){
 unsigned sel=regs[0x34]&15u;gpio_reads++;
 unsigned level=sel==9?((gpio_reads/150)&1u):
                sel==11?((gpio_reads/30)&1u):
                sel>=12&&sel<=14?((gpio_reads/2)&1u):1u;
 return level?2u:0u;
}
#define MIC_ROUTES_HOST_TEST 1
#include "../../../c/k6/mod/sotto_mic_routes.c"

static uint32_t clock_ms,next_block,sample_idx,cumulative_ones;
static pcm_ring_t*ring;
static unsigned tx_on_count,tx_off_count,stream_on_count,stream_off_count,clear_count,result_count;
static int tone_on=1,uart_mode=0,fail_switch=0;
static uint16_t saved47;
static char output[64];
static void advance(uint32_t now){
 while(ring && next_block+10<=now){
  next_block+=10;
  if((uint8_t)(ring->produced-ring->consumed)>=4){ring->lost=1;return;}
  unsigned slot=(ring->produced&3u)*80u;
  unsigned af=(regs[0x47]>>8)&15u;
  for(unsigned j=0;j<80;j++){
   int32_t amp=(tone_on && af==9)?6000:0;
   double wave=sin(6.283185307179586*(sample_idx&7)/8.0);
   ring->pcm[slot+j]=(int16_t)lrint(amp*wave);
   cumulative_ones+=(tone_on && af==9)?(uint32_t)(24+8*wave):48u;
   sample_idx++;
  }
  ring->produced=(uint8_t)(ring->produced+1u);
 }
}
static uint32_t ms(void){clock_ms++;advance(clock_ms);return clock_ms;}
static void delay_us(uint32_t us){clock_ms+=(us+999)/1000;advance(clock_ms);}
static int key(void){return 0;}
static void*scratch(uint32_t *n){static uint32_t buf[400];*n=sizeof(buf);return buf;}
static int tx_on(uint32_t n){
 if(n!=4500||tx_on_count)return -1;
 tx_on_count++;
 /* Hardware TX mode changes non-GP0 control outputs (e.g. LED), so
  * reading REG_33 before entering TX gives a stale restore image. */
 regs[0x33]=0xd320;
 return 0;
}
static void tx_off(void){tx_off_count++;regs[0x33]=0xd300;}
static uint16_t fd_read(uint8_t r){return regs[r];}
static int fd_write(uint8_t r,uint16_t v){
 if(r==0x34&&fail_switch)return -1;
 if(r==0x33 && ((v^regs[r])&~0x0101u))return -1;
 regs[r]=v;return 0;
}
static int stream(uint32_t op,void *p,uint32_t n){
 if(op==3)return uart_mode?-1:1;
 if(op==2)return 6284;
 if(op==4)return ring?(int)cumulative_ones:-1;
 if(op==0){
  if(!tx_on_count||n!=644||!p||ring)return -1;
  ring=(pcm_ring_t*)p;next_block=clock_ms;sample_idx=0;cumulative_ones=0;
  saved47=regs[0x47];regs[0x47]=(regs[0x47]&~0x0f00u)|0x0400u;
  stream_on_count++;return 0;
 }
 if(op==1){if(!ring)return -1;ring=0;regs[0x47]=saved47;stream_off_count++;return 0;}
 return -1;
}
static void lcd_text(int n,const char *s){(void)n;(void)s;}
static int lcd_flush(void){return 0;}
static void say(const char*s){
 if(strcmp(s,"MAB1:CLR")==0){clear_count++;output[0]=0;return;}
 if(strncmp(s,"MAB1:",5)==0){if(strlen(s)!=49)abort();strcpy(output,s);result_count++;}
}
int main(int argc,char**argv){
 uart_mode=argc>1&&strcmp(argv[1],"uart")==0;
 fail_switch=argc>1&&strcmp(argv[1],"error")==0;
 tone_on=!(argc>1&&strcmp(argv[1],"silent")==0);
 regs[0x33]=0xd300;regs[0x34]=0x0;regs[0x47]=0x6140;
 regs[0x64]=tone_on?650:305;
 k6_api_t api={0};api.abi=1;api.level=31;api.say=say;api.ms=ms;
 api.delay_us=delay_us;api.key_down=key;api.scratch=scratch;
 api.tx_on=tx_on;api.tx_off=tx_off;api.fd_read=fd_read;
 api.fd_write=fd_write;api.mic_stream=stream;
 api.lcd_text=lcd_text;api.lcd_flush=lcd_flush;
 int rc=k6_main(&api);
 if(uart_mode){
  if(rc!=-4||tx_on_count||result_count||clear_count!=1)return 11;
  puts("UART EXCLUSION PASS");return 0;
 }
 if(fail_switch){
  if(rc>=0||result_count!=1||!tx_off_count||!stream_off_count||
     regs[0x33]!=0xd300||regs[0x34]!=0||regs[0x47]!=0x6140)return 12;
  printf("encoded=%s\n",output);
  puts("FAILED WRITE CLEANUP PASS");return 0;
 }
 if(rc!=9||!tx_on_count||!tx_off_count||!stream_on_count||!stream_off_count||
    ring||result_count!=1||clear_count!=1||regs[0x33]!=0xd300||
    regs[0x34]!=0||regs[0x47]!=0x6140)return 13;
 printf("encoded=%s\n",output);
 puts("ROUTES RESULT AND RESTORE PASS");return 0;
}
