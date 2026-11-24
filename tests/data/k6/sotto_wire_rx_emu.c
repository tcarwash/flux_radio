/* Offline adapter for the selected development tree's real DV wire decoder.
 * Input: count, then count*66 physical bytes. Output: count*64 decoded records.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../../../c/k6/k6_mod.h"
#include "sotto_dv_wire.c"
int k6_main(const k6_api_t *a){
    uint32_t room,*s=a->scratch(&room);
    if(!s||room<4)return -1;
    unsigned n=s[0],i,j;
    if(n>31u || 4u+130u*n>room)return -2;
    uint8_t *raw=(uint8_t*)(s+1),*out=raw+66*n;
    for(i=0;i<n;i++){
        uint8_t *r=out+64*i,id=0;
        unsigned count=0,off=0,coff=0;
        dv_session_ctrl_t c;
        for(j=0;j<64;j++)r[j]=0;
        unsigned v=flux_dv_wire_rx66(raw+66*i,r+16,&id,&count,&off);
        unsigned k=flux_dv_wire_session_rx66(raw+66*i,&c,&coff);
        if(v+k!=1){for(j=0;j<64;j++)r[j]=0;continue;}
        if(v){
            r[0]=1;r[1]=(uint8_t)off;r[2]=id;r[3]=(uint8_t)count;
        }else{
            r[0]=c.kind+1u;r[1]=(uint8_t)coff;r[2]=c.first_id;r[3]=c.last_frames;
            for(j=0;j<4;j++){r[4+j]=(uint8_t)(c.tag_hi>>(24-8*j));r[8+j]=(uint8_t)(c.tag_lo>>(24-8*j));}
            r[12]=c.last_id;r[13]=c.segments;
        }
    }
    return (int)n;
}
