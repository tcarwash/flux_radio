/* GP0/PB1 -> DMA1 CH1 HT/TC ISR -> fixed-point 8kHz PCM ring.
 * Experimental module-owned IRQ9 handler, no firmware/base flash change.
 * Module PIC has no writable statics: irq_context pointer is stored in a
 * word embedded in the MODULE'S SRAM code, and zeroed on teardown.
 * Starts only with txdog SRAM vectors mapped, TIM17/DMA1 CH1 unowned.
 * No RFIC/SPI/flash/UART callbacks inside interrupt. SPDX: MIT. */
#ifndef SOTTO_GP0_IRQ_CAPTURE_H
#define SOTTO_GP0_IRQ_CAPTURE_H
#include <stdint.h>
#include "sotto_gp0_pcm.h"
#include "sotto_live_engine.h"
#define GPIRQ_RING_SIZE 1024u
#define GPIRQ_HALF 512u
#define GPIRQ_VECTOR (0x20000000u+4u*(16u+9u))
#define GPIRQ_DMA_ISR 0x40020000u
#define GPIRQ_DMA_IFCR 0x40020004u
#define GPIRQ_DMA_CCR1 0x40020008u
#define GPIRQ_DMA_CNDTR1 0x4002000cu
#define GPIRQ_DMA_CPAR1 0x40020010u
#define GPIRQ_DMA_CMAR1 0x40020014u
#define GPIRQ_NVIC_ISER 0xe000e100u
#define GPIRQ_NVIC_ICER 0xe000e180u
#define GPIRQ_NVIC_ICPR 0xe000e280u
#define GPIRQ_NVIC_IPR9 0xe000e409u
#define GPIRQ_RCC_AHB 0x40021014u
#define GPIRQ_TIM17 0x40014800u
#define GPIRQ_TIM17_CNT 0x40014824u
#define GPIRQ_RCC_APB2 0x40021018u
#define GPIRQ_CFGR1 0x40010000u
#define GPIRQ_FLAG_HT 0x4u
#define GPIRQ_FLAG_TC 0x2u
#define GPIRQ_FLAG_TE 0x8u
#define GPIRQ_IRQ_ENABLE 0x0Eu
#define GPIRQ_CHEN (1u<<0)
#define GPIRQ_NVIC_MASK (1u<<9)
#ifndef GPIRQ_HOST_TEST
static uint32_t gpirq_rd(uint32_t a){return *(volatile const uint32_t *)(uintptr_t)a;}
static void gpirq_wr(uint32_t a,uint32_t v){*(volatile uint32_t *)(uintptr_t)a=v;}
static uint8_t gpirq_rd8(uint32_t a){return *(volatile const uint8_t *)(uintptr_t)a;}
static void gpirq_wr8(uint32_t a,uint8_t v){*(volatile uint8_t *)(uintptr_t)a=v;}
#else
extern uint32_t gpirq_host_rd(uint32_t);
extern void gpirq_host_wr(uint32_t,uint32_t);
#define gpirq_rd gpirq_host_rd
#define gpirq_wr gpirq_host_wr
#define gpirq_rd8(a) ((uint8_t)gpirq_host_rd(a))
#define gpirq_wr8(a,v) gpirq_host_wr((a),(v))
#endif

typedef struct {
 sl_pcm_t *pcm;               /* 4x80 sample handoff; read only in main */
 uint8_t *raw;                 /* exactly 1024 byte aligned DMA ring */
 gp0_pcm_t dec;
 uint32_t old_vector;
 uint32_t interrupts;
 uint32_t peak_cycles;
 uint32_t work_cycles;
 uint32_t missed_halves;
 uint32_t actual_halves;
 uint8_t expect_tc;            /* 0=expect first HT, 1=expect TC */
 uint8_t active;
 uint8_t error;                /* 1=DMA transfer, 2=flags/order, 3=PCM overflow */
 uint8_t previous_active;
 uint8_t prior_priority;
} gp0_irq_audio_t;

/* On the K6 all loadable modules are SRAM-only PIC and may not have .bss.
 * The vector handler's PC-relative literal is a writable code word ONLY
 * while the verified loader owns this module's SRAM block. The body never
 * accesses a global and receives the context through R0. */
#ifndef GPIRQ_HOST_TEST
extern void gp0_irq_entry(void);
/* No direct &gp0_irq_context_word references in C: that would emit
 * R_ARM_ABS32 and fail K6's two-load-address PIC identity check. */
extern void gp0_irq_store_context(uint32_t);
extern uint32_t gp0_irq_load_context(void);
void gp0_irq_body(gp0_irq_audio_t *s);
__asm__(
  ".syntax unified\n"
  ".thumb\n"
  ".section .text.gp0_irq_entry,\"ax\",%progbits\n"
  ".align 2\n"
  ".global gp0_irq_entry\n"
  ".thumb_func\n"
  "gp0_irq_entry:\n"
  "push {r4, lr}\n"
  "adr r0, gp0_irq_context_word\n"
  "ldr r0, [r0]\n"
  "bl gp0_irq_body\n"
  "pop {r4, pc}\n"
  ".align 2\n"
  ".global gp0_irq_store_context\n"
  ".thumb_func\n"
  "gp0_irq_store_context:\n"
  "adr r1, gp0_irq_context_word\n"
  "str r0, [r1]\n"
  "bx lr\n"
  ".align 2\n"
  ".global gp0_irq_load_context\n"
  ".thumb_func\n"
  "gp0_irq_load_context:\n"
  "adr r0, gp0_irq_context_word\n"
  "ldr r0, [r0]\n"
  "bx lr\n"
  ".align 2\n"
  ".global gp0_irq_context_word\n"
  "gp0_irq_context_word:\n"
  ".word 0\n"
  ".previous\n"
);
#else
static uint32_t gp0_irq_context_word;
static void gp0_irq_store_context(uint32_t v){gp0_irq_context_word=v;}
static uint32_t gp0_irq_load_context(void){return gp0_irq_context_word;}
#endif

/* Fast ISR: 512 raw PB1 bytes, 8 density counts, generally <one 8kHz
 * sample per half IRQ. No API call, no sqrt, no slow serial output.
 * GP0 high rate ~5.5M bytes/s -> ~93us per half; an interrupt must
 * preempt codec/FSK work rather than waiting for the foreground loop. */
void gp0_irq_body(gp0_irq_audio_t *s){
 if(!s||!s->active){gpirq_wr(GPIRQ_DMA_IFCR,15u);return;}
 uint32_t flags=gpirq_rd(GPIRQ_DMA_ISR)&15u;
 uint32_t start=gpirq_rd(GPIRQ_TIM17_CNT)&65535u;
 if(flags&GPIRQ_FLAG_TE){s->error=1;goto fail;}
 /* Both flags pending: at least 512 bytes may already have been replaced
  * while our ISR was starved. Fail closed; never claim contiguous audio. */
 if((flags&(GPIRQ_FLAG_HT|GPIRQ_FLAG_TC))==(GPIRQ_FLAG_HT|GPIRQ_FLAG_TC)){
  s->error=2;s->missed_halves++;goto fail;
 }
 uint32_t expected=s->expect_tc?GPIRQ_FLAG_TC:GPIRQ_FLAG_HT;
 if(!(flags&expected)){
  s->error=2;s->missed_halves++;goto fail;
 }
 /* Ack FIRST so a new IRQ isn't mistaken for the current half. */
 gpirq_wr(GPIRQ_DMA_IFCR,15u);
 uint32_t off=s->expect_tc?GPIRQ_HALF:0;
 s->expect_tc^=1u;s->interrupts++;
 for(uint32_t i=0;i<GPIRQ_HALF;i+=64u){
  const uint32_t *p=(const uint32_t *)(s->raw+off+i);
  uint32_t acc=0;
  for(unsigned j=0;j<16u;j++)acc+=p[j]&0x02020202u;
  acc>>=1;
  acc=(acc&0x00ff00ffu)+((acc>>8)&0x00ff00ffu);
  unsigned bits=(unsigned)((acc&0xffffu)+(acc>>16));
  int16_t sample=0;
  int r=gp0_pcm_push(&s->dec,bits,&sample);
  if(r<0|| (r==1&&sl_pcm_sample(s->pcm,sample))){s->error=3;goto fail;}
 }
 s->actual_halves++;
 {uint32_t end=gpirq_rd(GPIRQ_TIM17_CNT)&65535u;
  uint32_t elapsed=(end-start)&65535u;
  s->work_cycles+=elapsed;
  if(elapsed>s->peak_cycles)s->peak_cycles=elapsed;
  /* TIM17 is running at ~96MHz. 512 bytes at 5.5MS/s lasts ~8.9k
   * counter ticks. A >8k peak is already too close to the edge. */
  if(elapsed>9000u){s->error=4;goto fail;}
 }
 return;
fail:
 /* Do not leave hardware raising unhandled IRQs. Foreground restores
  * the vector and radio; this ISR only disables the DMA source. */
 gpirq_wr(GPIRQ_DMA_CCR1,0);
 gpirq_wr(GPIRQ_DMA_IFCR,15u);
 s->active=0;
}

/* caller MUST have the RFIC mic route lease (API30 op7) and GP0 select12,
 * TIM17 clock + DMA clock enabled, and PIC module loaded in writable SRAM.
 * Caller owns vector/clock/TIM17 restore. DMA channel1 and IRQ9 checked.
 * No module other than the active owner may attempt this. */
static int gp0_irq_arm(gp0_irq_audio_t*s,sl_pcm_t*p,uint8_t*raw,uint32_t fs){
 if(!s||!p||!raw||((uintptr_t)raw&3u)||
    fs<35000u||fs>130000u)return -1;
 if((gpirq_rd(GPIRQ_CFGR1)&3u)!=3u ||
    gpirq_rd(GPIRQ_VECTOR)!=gpirq_rd(0x00000064u) ||
    (gpirq_rd(GPIRQ_DMA_CCR1)&GPIRQ_CHEN) ||
    (gpirq_rd(GPIRQ_NVIC_ISER)&GPIRQ_NVIC_MASK) ||
    gp0_irq_load_context()) return -2;
 for(unsigned i=0;i<sizeof(*s);i++)((uint8_t*)s)[i]=0;
 s->pcm=p;s->raw=raw;
 gp0_pcm_init(&s->dec,fs);
 s->old_vector=gpirq_rd(GPIRQ_VECTOR);
 s->prior_priority=gpirq_rd8(GPIRQ_NVIC_IPR9);
 s->active=1;
 /* All IRQ setup runs in main context with interrupts masked. IRQ9 isn't
  * enabled until the handler code and its data pointer are published. */
#ifndef GPIRQ_HOST_TEST
 uint32_t prior_primask;
 __asm volatile("mrs %0, primask\n cpsid i" : "=r"(prior_primask) :: "memory");
 gp0_irq_store_context((uint32_t)(uintptr_t)s);
 gpirq_wr(GPIRQ_VECTOR,(uint32_t)(uintptr_t)&gp0_irq_entry|1u);
#else
 gp0_irq_store_context((uint32_t)(uintptr_t)s);
 gpirq_wr(GPIRQ_VECTOR,0x20002001u);
#endif
 gpirq_wr8(GPIRQ_NVIC_IPR9,0x80u);
 gpirq_wr(GPIRQ_NVIC_ICPR,GPIRQ_NVIC_MASK);
 gpirq_wr(GPIRQ_DMA_CCR1,0);
 gpirq_wr(GPIRQ_DMA_IFCR,15u);
 gpirq_wr(GPIRQ_DMA_CPAR1,0x48000410u);
 gpirq_wr(GPIRQ_DMA_CMAR1,(uint32_t)(uintptr_t)raw);
 gpirq_wr(GPIRQ_DMA_CNDTR1,GPIRQ_RING_SIZE);
 /* CIRC+MINC+VeryHigh, HTIE+TCIE+TEIE. Forced DMA1 ch1 requests
  * already established on this K6 B-chip; TIM17 only times ISR cycles. */
 gpirq_wr(GPIRQ_DMA_CCR1,(3u<<12)|(1u<<7)|(1u<<5)|GPIRQ_IRQ_ENABLE|1u);
 gpirq_wr(GPIRQ_NVIC_ISER,GPIRQ_NVIC_MASK);
#ifndef GPIRQ_HOST_TEST
 __asm volatile("msr primask, %0" :: "r"(prior_primask) : "memory");
#endif
 return 0;
}
static void gp0_irq_disarm(gp0_irq_audio_t*s){
 if(!s)return;
#ifndef GPIRQ_HOST_TEST
 uint32_t prior_primask;
 __asm volatile("mrs %0, primask\n cpsid i" : "=r"(prior_primask) :: "memory");
#endif
 gpirq_wr(GPIRQ_DMA_CCR1,0);
 gpirq_wr(GPIRQ_NVIC_ICER,GPIRQ_NVIC_MASK);
 gpirq_wr(GPIRQ_DMA_IFCR,15u);
 gpirq_wr(GPIRQ_NVIC_ICPR,GPIRQ_NVIC_MASK);
 gpirq_wr(GPIRQ_VECTOR,s->old_vector);
 gpirq_wr8(GPIRQ_NVIC_IPR9,s->prior_priority);
 gp0_irq_store_context(0);
 s->active=0;
#ifndef GPIRQ_HOST_TEST
 __asm volatile("msr primask, %0" :: "r"(prior_primask) : "memory");
#endif
}
#endif
