/* SPDX-License-Identifier: MIT
 * Firmware-owned channel editor field page. Explicit wire ABI, never Rust layout.
 * args: 32-byte channel record, little-endian shift/offset, editing flag.
 * Return text in caller's buffer (no persisted pointers into the arena).
 */
#include <stdint.h>
#include <stddef.h>
#include "channel_tones.h"
/* Thumb-1 has no hardware divide. Keep the helper inside this page. */
uint32_t __aeabi_uidiv(uint32_t n,uint32_t d){
    if(!d)return 0;
    uint32_t q=0,bit=1;
    while(d<= (n>>1)){d<<=1;bit<<=1;}
    while(bit){if(n>=d){n-=d;q|=bit;}d>>=1;bit>>=1;}
    return q;
}
static char *str(char *p,const char *s){while(*s)*p++=*s++;return p;}
static char *num(char *p,uint32_t n,unsigned width){char b[10];unsigned k=0;do{b[k++]=(char)('0'+n%10);n/=10;}while(n);while(k<width)b[k++]='0';while(k)*p++=b[--k];return p;}
static uint32_t bcd(const uint8_t *x){uint32_t n=0;for(int i=3;i>=0;i--)n=n*100+(x[i]>>4)*10+(x[i]&15);return n;}
static uint32_t le32(const uint8_t *x){return x[0]|(uint32_t)x[1]<<8|(uint32_t)x[2]<<16|(uint32_t)x[3]<<24;}
static unsigned le16(const uint8_t *x){return x[0]|(unsigned)x[1]<<8;}
static char *pair(char *p,uint32_t a,uint32_t b,unsigned width){p=num(p,a,1);*p++='.';return num(p,b,width);}
static int render(const uint8_t *a,unsigned field,char *out){char *p=out;uint32_t hz;unsigned t;switch(field){
case 0: hz=bcd(a);p=pair(p,hz/100000,hz%100000,5);break;
case 1:p=str(p,a[32]==1?"+":a[32]==2?"-":"OFF");break;
case 2:hz=le32(a+33);p=pair(p,hz/1000000,hz%1000000/100,4);break;
case 3:case 4:t=le16(a+8+2*(field-3));if(t>250){p=pair(p,t/10,t%10,1);p=str(p,"Hz");}else if(t&&t<=210){unsigned d=dcs[(t-1)%105];*p++='D';*p++='0'+(d>>6&7);*p++='0'+(d>>3&7);*p++='0'+(d&7);*p++=t>105?'I':'N';}else p=str(p,"OFF");break;
case 5:p=str(p,a[14]==1?"MID":a[14]==2?"LOW":a[14]==3&&a[38]?"EL":"HIGH");break;
case 6:p=str(p,a[15]&64?"NARROW":"WIDE");break;
case 7:p=str(p,a[15]&4?"ON":"OFF");break;
case 8:p=str(p,a[15]&8?"ON":"OFF");break;
case 9:t=a[12]&31;if(t<20){*p++='#';p=num(p,t+1,2);}else p=str(p,"NONE");break;
case 10:for(unsigned i=20;i<32&&a[i]&&a[i]!=255;i++){if(a[i]>=128){p=out;break;}*p++=a[i];}break;
case 12:if(a[37])p=str(p,"Sure? MENU");break;
default:break;}return (int)(p-out);}
/* op 0 read; op 1/2 step up/down; op 3 set (restore or zero). */
static int scalar(uint8_t *a,unsigned field,unsigned op,int val){
 int cur=0,lo=0,hi=0,wrap=0;unsigned pos=0,mask=0,t;
 switch(field){
 case 1:cur=a[32];hi=2;break;
 case 3:case 4:pos=8+2*(field-3);t=le16(a+pos);lo=-1;hi=259;wrap=1;cur=-1;
 if(t>250){for(unsigned i=0;i<50;i++)if(ctcss[i]==t){cur=i;break;}}else if(t&&t<=210)cur=49+t;break;
 case 5:hi=a[38]?3:2;cur=a[14]<=hi?a[14]:0;break;
 case 6:mask=64;goto flag;case 7:mask=4;goto flag;case 8:mask=8;flag:cur=!!(a[15]&mask);hi=1;break;
 case 9:lo=-1;hi=19;t=a[12]&31;cur=t<20?(int)t:-1;break;
 default:return 0;
 }
 if(!op)return cur;
 if(op==1||op==2){if(mask)val=1-cur;else if(op==1)val=cur>=hi?(wrap?lo:hi):cur+1;else val=cur<=lo?(wrap?hi:lo):cur-1;}
 if(val<lo||val>hi)return cur;
 switch(field){
 case 1:a[32]=val;break;
 case 3:case 4:t=val<0?0:val<50?ctcss[val]:val-49;a[pos]=t;a[pos+1]=t>>8;break;
 case 5:a[14]=val;break;
 case 6:case 7:case 8:a[15]=(a[15]&~mask)|(val?mask:0);break;
 case 9:a[12]=val<0?31:val;break;
 }
 return val;
}
__attribute__((section(".kp_tab"),used)) const uintptr_t kp_tab[]={0x0212504b,(uintptr_t)kp_tab,(uintptr_t)render,(uintptr_t)scalar};
