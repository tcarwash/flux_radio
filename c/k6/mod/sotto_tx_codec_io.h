/* Read-only Sotto slim tables, explicitly owned by the encoder instance.
 * SPDX-License-Identifier: MIT */
#ifndef SOTTO_TX_CODEC_IO_H
#define SOTTO_TX_CODEC_IO_H
#include <stdint.h>
#include "dv_live_slim_dct.h"

#define SOTTO_CB_BASE 0x100000u
#define SOTTO_CB_SIZE 30720u
#define SOTTO_CB_CRC 0xd5856ecdu
#define SOTTO_DECK_SIZE 4182u
#define SOTTO_DECK_CRC 0x0de43427u
#define SOTTO_DECK_OFF 1296u
#define SOTTO_DECK_VEC 1622u

typedef struct {
    const k6_api_t *api;
    uint32_t deck;
    unsigned fault;
} sotto_io_t;

static int sio_read(sotto_io_t *c,uint32_t addr,void *buf,unsigned n) {
    unsigned i;
    if(!c->fault && c->api->fl_read(addr,buf,n)==0) return 0;
    c->fault=1;
    for(i=0;i<n;i++)((uint8_t*)buf)[i]=0;
    return -1;
}
static uint32_t sio_crc(uint32_t crc,const uint8_t *p,unsigned n) {
    unsigned i,j;
    for(i=0;i<n;i++){
        crc^=p[i];
        for(j=0;j<8;j++)crc=(crc>>1)^(0xedb88320u & (0u-(crc&1u)));
    }
    return crc;
}
static int sio_check(sotto_io_t *c,uint32_t addr,unsigned n,uint32_t want) {
    uint8_t buf[128];
    unsigned off;
    uint32_t crc=~0u;
    for(off=0;off<n;off+=sizeof(buf)){
        unsigned k=n-off;
        if(k>sizeof(buf))k=sizeof(buf);
        if(sio_read(c,addr+off,buf,k))return -1;
        crc=sio_crc(crc,buf,k);
    }
    return (~crc==want)?0:-1;
}
static uint32_t sio_le32(const uint8_t *p){
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static int sio_init(sotto_io_t *c,const k6_api_t *api){
    uint8_t h[32];
    unsigned slot;
    c->api=api;c->deck=0;c->fault=0;
    if(sio_read(c,SOTTO_CB_BASE,h,32) ||
       h[0]!='K'||h[1]!='6'||h[2]!='C'||h[3]!='B'||
       h[4]!=1||h[5]!=0||h[6]!=20||h[7]!=0||
       sio_le32(h+8)!=SOTTO_CB_SIZE||
       h[12]!=4||h[13]!=9||h[14]!=9||h[15]!=8||h[16]!=8||
       sio_le32(h+24)!=SOTTO_CB_CRC||
       ~sio_crc(~0u,h,28)!=sio_le32(h+28)||
       sio_check(c,SOTTO_CB_BASE+32,SOTTO_CB_SIZE,SOTTO_CB_CRC))goto bad;
    /* Current fixed-slot library; packed KUL2 requires separate discovery API. */
    for(slot=0;slot<32u;slot++){
        uint32_t a=0x110000u+slot*0x4000u;
        if(sio_read(c,a,h,32))goto bad;
        if(h[0]!='K'||h[1]!='6'||h[2]!='M'||h[3]!=1||
           h[13]!=5u||h[15]!=slot||
           (unsigned)(h[4]|h[5]<<8)!=SOTTO_DECK_SIZE||
           h[16]!='K'||h[17]!='6'||h[18]!='S'||h[19]!='C')continue;
        if(sio_check(c,a+16,SOTTO_DECK_SIZE,SOTTO_DECK_CRC)==0){
            c->deck=a+16;return 0;
        }
        if(c->fault)goto bad;
    }
bad:
    c->fault=1;return -1;
}
static const signed char *sio_cb(sotto_io_t *c,int s,int v,signed char *buf){
    static const uint16_t base[4]={0,512,1024,1280};
    if(s<0||s>3||v<0||v>=(s<2?512:256)){
        c->fault=1;s=0;v=0;
    }
    (void)sio_read(c,SOTTO_CB_BASE+32+(base[s]+(unsigned)v)*20u,buf,20);
    return buf;
}
static unsigned sio_cellbase(int s){return s==0?0u:s==1?64u:128u;}
static const signed char *sio_cen(sotto_io_t *c,int s,int first,signed char *buf){
    if(s<0||s>2||first<0||first+16>(s==2?32:64)){
        c->fault=1;s=0;first=0;
    }
    (void)sio_read(c,c->deck+16+(sio_cellbase(s)+(unsigned)first)*8u,buf,128);
    return buf;
}
static int sio_cell(sotto_io_t *c,int s,int cell,int16_t *v){
    uint8_t ab[4];unsigned a,b,i;
    if(s<0||s>2||cell<0||cell>=(s==2?32:64))goto bad;
    if(sio_read(c,c->deck+SOTTO_DECK_OFF+2u*(sio_cellbase(s)+(unsigned)s+(unsigned)cell),ab,4))goto bad;
    a=(unsigned)ab[0]|(unsigned)ab[1]<<8;
    b=(unsigned)ab[2]|(unsigned)ab[3]<<8;
    if(b<a||b>1280u||b-a>26u)goto bad;
    if(b>a && sio_read(c,c->deck+SOTTO_DECK_VEC+2u*a,v,2u*(b-a)))goto bad;
    for(i=0;i<b-a;i++)if(v[i]<0||v[i]>=(s==2?256:512))goto bad;
    return (int)(b-a);
bad:
    /* Supply a harmless candidate so upstream's current call completes
       deterministically even when all table reads fail; caller must abort. */
    c->fault=1;v[0]=0;return 1;
}
#define SLIM_CB_FETCH(s,v,buf) sio_cb(io,s,v,buf)
#define SLIM_IVF_CEN(s,f,buf) sio_cen(io,s,f,buf)
#define SLIM_IVF_CELL(s,c,v) sio_cell(io,s,c,v)
#endif
