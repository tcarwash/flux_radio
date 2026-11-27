/* Host-only minimum link of the production pager's receive wire validator.
 * Tests CRC/PN9/magic acceptance without needing a radio or speech decoder.
 * SPDX-License-Identifier: MIT */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../../../c/k6/sotto_pager.h"

static int try_magic(unsigned char magic) {
    unsigned char b[SP_PAYLOAD_L];
    const unsigned char *w=sp_pn9();
    unsigned crc;
    unsigned i;
    memset(b,0,sizeof b);
    b[0]=magic;
    b[1]=7;
    b[5]=8;
    crc=sp_crc16(b,SP_PAYLOAD_L-2);
    b[62]=(unsigned char)(crc>>8);
    b[63]=(unsigned char)crc;
    for(i=0;i<sizeof b;++i)b[i]^=w[i];
    assert(sp_check(b,SP_PAYLOAD_L)==(sp_packet(b,SP_PAYLOAD_L)!=SP_BAD_MAGIC));
    return sp_check(b,SP_PAYLOAD_L);
}
int main(void) {
    static const unsigned char magics[]={SP_MAGIC,SP_MAGIC_LR,SP_MAGIC_600,SP_MAGIC_400,
                                          SP_MAGIC|0x10,SP_MAGIC_LR|0x10,
                                          SP_MAGIC_600|0x10,SP_MAGIC_400|0x10,0xaa};
    unsigned i,accept=0;
    for(i=0;i<sizeof magics;++i){
        int x=try_magic(magics[i]);
        if(x) accept++;
#ifdef SP_ONLY_1200
        assert(x==(magics[i]==SP_MAGIC_LR || magics[i]==(SP_MAGIC_LR|0x10)));
#else
        assert(x==(magics[i]!=0xaa));
#endif
        printf("%02X:%u%s",magics[i],(unsigned)x,i+1==sizeof magics?"\n":" ");
    }
#ifdef SP_ONLY_1200
    assert(accept==2);
#else
    assert(accept==8);
#endif
    return 0;
}
