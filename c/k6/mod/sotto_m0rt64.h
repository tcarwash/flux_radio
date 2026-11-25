/* Small software 64-bit runtime for the Cortex-M0 RX codec fixture.
 * No writable globals; fixed-sized loops; unsigned division uses 32-bit limbs.
 * OFFLINE qualification only, not yet a radio-release runtime. MIT. */
#ifndef SOTTO_M0RT64_H
#define SOTTO_M0RT64_H
#include <stdint.h>

typedef unsigned long long rx64_t;
static rx64_t rx64_pair(uint32_t lo, uint32_t hi) {
    return ((rx64_t)hi << 32) | lo;
}

rx64_t __aeabi_llsl(rx64_t n, uint32_t shift) {
    uint32_t lo=(uint32_t)n, hi=(uint32_t)(n>>32);
    if (shift>=64) return 0;
    if (shift>=32) return rx64_pair(0,lo<<(shift-32));
    if (!shift) return n;
    return rx64_pair(lo<<shift,(hi<<shift)|(lo>>(32-shift)));
}
rx64_t __aeabi_llsr(rx64_t n, uint32_t shift) {
    uint32_t lo=(uint32_t)n, hi=(uint32_t)(n>>32);
    if (shift>=64) return 0;
    if (shift>=32) return rx64_pair(hi>>(shift-32),0);
    if (!shift) return n;
    return rx64_pair((lo>>shift)|(hi<<(32-shift)),hi>>shift);
}
rx64_t __aeabi_lasr(rx64_t n, uint32_t shift) {
    uint32_t lo=(uint32_t)n;
    int32_t hi=(int32_t)(n>>32);
    if (shift>=64) return rx64_pair(hi<0?0xffffffffu:0,hi<0?0xffffffffu:0);
    if (shift>=32) return rx64_pair((uint32_t)(hi>>(shift-32)),hi<0?0xffffffffu:0);
    if (!shift) return n;
    return rx64_pair((lo>>shift)|((uint32_t)hi<<(32-shift)),(uint32_t)(hi>>shift));
}
rx64_t __aeabi_lmul(rx64_t a, rx64_t b) {
    uint32_t x=(uint32_t)a,y=(uint32_t)b;
    uint32_t p0=(x&65535u)*(y&65535u);
    uint32_t p1=(x&65535u)*(y>>16);
    uint32_t p2=(x>>16)*(y&65535u);
    uint32_t p3=(x>>16)*(y>>16);
    uint32_t mid=(p0>>16)+(p1&65535u)+(p2&65535u);
    uint32_t lo=(p0&65535u)|(mid<<16);
    uint32_t hi=p3+(p1>>16)+(p2>>16)+(mid>>16);
    hi+=x*(uint32_t)(b>>32)+(uint32_t)(a>>32)*y;
    return rx64_pair(lo,hi);
}

/* AAPCS 64-by-64 divide: quotient in r0:r1, remainder in r2:r3. */
__attribute__((used, noinline))
rx64_t rx_udivmod64(rx64_t n, rx64_t d, rx64_t *rem) {
    uint32_t nh=(uint32_t)(n>>32),nl=(uint32_t)n;
    uint32_t dh=(uint32_t)(d>>32),dl=(uint32_t)d;
    uint32_t rh=0,rl=0,qh=0,ql=0;
    int i;
    if (!(dh|dl)) { *rem=n; return ~(rx64_t)0; }
    for(i=63;i>=0;i--) {
        uint32_t bit=i>=32?(nh>>(i-32))&1u:(nl>>i)&1u;
        uint32_t extra=rh>>31;
        rh=(rh<<1)|(rl>>31);
        rl=(rl<<1)|bit;
        if(extra||rh>dh||(rh==dh&&rl>=dl)) {
            uint32_t prior=rl;
            rl-=dl;
            rh-=dh+(prior<dl);
            if(i>=32) qh|=1u<<(i-32); else ql|=1u<<i;
        }
    }
    *rem=rx64_pair(rl,rh);
    return rx64_pair(ql,qh);
}
__attribute__((naked))
void __aeabi_uldivmod(void) {
    __asm volatile(
        "push {r4, lr}\n"
        "sub sp, #16\n"
        "add r4, sp, #8\n"
        "str r4, [sp]\n"
        "bl rx_udivmod64\n"
        "ldr r2, [sp, #8]\n"
        "ldr r3, [sp, #12]\n"
        "add sp, #16\n"
        "pop {r4, pc}\n"
    );
}
#endif
