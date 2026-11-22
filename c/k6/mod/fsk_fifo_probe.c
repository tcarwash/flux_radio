/* Flux FSK FIFO feasibility probe, READ-ONLY FD6818B register snapshot.
 * Does not call fd_write(), TX API, RAM patch or calibration/flash write.
 * REG_5E bit fields from old FD6818 are *not yet verified* on B silicon.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 1
#include "../k6_mod.h"

static void hex4(char *p,unsigned v){
    static const char digs[]="0123456789ABCDEF";
    unsigned k;
    for(k=0;k<4;k++)p[k]=digs[(v>>(12-4*k))&15u];
}
int k6_main(const k6_api_t *a){
    static const unsigned char regs[]={0x00,0x02,0x0c,0x30,0x3f,0x59,0x5e,0x5f};
    char s[60];
    unsigned i;
    if(!a || a->abi!=1 || a->level<1)return -3;
    if(a->fd_read(0x00)!=0x4829u)return -6;
    /* 0x5F is the destructive FSK FIFO data register and MUST NOT
       be read. The explicit skip below protects RX/TX FIFO state. */
    for(i=1;i<sizeof(regs);i++){
        unsigned reg=regs[i];
        if(reg==0x5fu)continue;
        s[0]='r';hex4(s+1,reg);
        s[5]='=';hex4(s+6,a->fd_read(reg));
        s[10]=0;
        a->say(s);
    }
    return 0;
}
