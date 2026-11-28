/* Linked ARM test: API30 op7 enables RFIC microphone without PA11/TIM17.
 * It must be exclusive, guarded and reversible, with PA muted. MIT. */
#define K6_MODULE_LEVEL 31
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t*a){
 uint32_t n;uint8_t*p;
 if(!a||a->abi!=1||a->level!=31||!a->mic_stream)return -2;
 p=(uint8_t*)a->scratch(&n);
 if(!p||n<700)return -3;
 if(a->mic_stream(7,0,0)!=-1)return -4; /* TX ownership mandatory */
 if(a->tx_on(2500)!=0)return -5;
 if(a->mic_stream(7,0,0)!=0){a->tx_off();return -6;}
 if(a->mic_stream(7,0,0)!=-1){a->tx_off();return -7;}
 if(a->mic_stream(0,p,644)!=-1){a->tx_off();return -8;}
 if(a->mic_stream(1,0,0)!=0){a->tx_off();return -9;}
 a->tx_off();
 return 17;
}
