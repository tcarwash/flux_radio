/* Offline-only Cortex-M0 test of the actual one-pin PA11 GPIO turn loop.
   No hardware or module entry point. Deterministic GPIO IDR=0 model. */
#include <stdint.h>
#include "pa11adc.h"
__attribute__((used)) uint32_t pa11_probe(uint32_t w, uint32_t hold, uint32_t m_in) {
  pa11_t c={0,0,0,0};
  return pa11_sample(m_in,hold,w,&c);
}
