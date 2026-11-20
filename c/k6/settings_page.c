/* SPDX-License-Identifier: MIT
 * Settings presentation, fixed scalar ABI; no hardware access or persistent state. */
#include <stdint.h>
#include "settings_ids.h"
/* Keep Thumb-1 division local to the overlay. Avoid compiler runtime ABI
 * dependencies and signed division by expressing all divisions as unsigned. */
uint32_t __aeabi_uidiv(uint32_t n,uint32_t d){
    if(!d)return 0;
    uint32_t q=0,bit=1;
    while(d<=(n>>1)){d<<=1;bit<<=1;}
    while(bit){if(n>=d){n-=d;q|=bit;}d>>=1;bit>>=1;}
    return q;
}
/* ARM EABI divmod returns quotient in r0, remainder in r1. */
#if defined(__arm__) || defined(__thumb__)
__attribute__((naked)) void __aeabi_uidivmod(void){
    __asm volatile("push {r0,r1,r4,lr}\nbl __aeabi_uidiv\npop {r1,r2,r4}\nmuls r2,r0\nsubs r1,r1,r2\npop {pc}");
}
#endif
static char *str(char *p,const char *s){while(*s)*p++=*s++;return p;}
static char *num(char *p,uint32_t n,unsigned width){char b[10];unsigned k=0;do{b[k++]=(char)('0'+n%10);n/=10;}while(n);while(k<width)b[k++]='0';while(k)*p++=b[--k];return p;}
static char *signed_num(char *p,int32_t n,int plus){if(n<0)*p++='-';else if(plus)*p++='+';return num(p,n<0?0u-(uint32_t)n:(uint32_t)n,1);}
static char *pair(char *p,uint32_t a,uint32_t b,unsigned width){p=num(p,a,1);*p++='.';return num(p,b,width);}
static int render(unsigned field,int32_t v,uint32_t aux,char *out){char *p=out;const char *s=0;switch(field){
case S_Tdr:case S_BusyLock:case S_TxForbid:case S_Beep:case S_ActionBeep:case S_Tail:case S_BootSnd:case S_Vox:case S_AprsDevInfo:case S_AprsBatVolt:s=v?"ON":"OFF";break;
case S_BootMode:s=v==1?"VOLT":v==2?"MSG":v==3?"LOGO":"NONE";break;
case S_Roge:s=v==1?"ROGER":v==2?"MDC1200":"OFF";break;
case S_AutoLk:s=v==1?"5S":v==2?"10S":v==3?"15S":"OFF";break;
case S_Abr:s=v==1?"5S":v==2?"10S":v==3?"15S":v==4?"20S":"OFF";break;
case S_Wn:s=v?"NARROW":"WIDE";break;
case S_TxPr:s=v==1?"MID":v==2?"LOW":v==3&&aux?"EL":"HIGH";break;
case S_ScanMd:s=v==0?"TIME":v==2?"STOP":"CARR";break;
case S_ChDisp:s=v==1?"NAME":v==2?"NAME+F":"FREQ";break;
case S_BkIn:s=v==1?"SEMI":v==2?"FULL":"OFF";break;
case S_AprsPath:s=v==1?"WIDE2-2":v==2?"ARISS":v==3?"DIRECT":"W1-1,W2-1";break;
case S_AprsPower:s=v==1?"MID":v==2?"HIGH":"LOW";break;
case S_Step:p=pair(p,aux/100,aux%100,2);s="k";break;
case S_BattCal:p=pair(p,aux/100,aux%100,2);s="V";break;
case S_VoxDly:p=pair(p,(uint32_t)(v+5)/10,(uint32_t)(v+5)%10,1);s="S";break;
case S_Rptrl:if(v){p=signed_num(p,v*100,0);s="MS";}else s="OFF";break;
case S_Rtone:p=num(p,aux,1);s="Hz";break;
case S_Scrm:case S_Save:if(v)p=signed_num(p,v,0);else s="OFF";break;
case S_Offse:p=pair(p,(uint32_t)v/1000000,(uint32_t)v%1000000/100,4);break;
case S_Rit:if(v)p=signed_num(p,v*10,1);else *p++='0';s="Hz";break;
case S_AprsFreq:p=pair(p,(uint32_t)v/1000000,(uint32_t)v%1000000/1000,3);break;
case S_AprsSsid:*p++='-';p=signed_num(p,v,0);break;
default:p=signed_num(p,v,0);break;
}if(s)p=str(p,s);return (int)(p-out);}
__attribute__((section(".kp_tab"),used)) const uintptr_t kp_tab[]={0x0114504b,(uintptr_t)kp_tab,(uintptr_t)render};
