/* Off-radio Cortex-M0 fixture: use the Sotto encoder's own codec resource
 * initializer to validate K6CB and K6SC CRC over real ABI28 callbacks.
 * No PTT, mic, PA or transmit. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 31
#define SOTTO_HOST_RESOURCES
#pragma clang diagnostic ignored "-Wunused-function"
#include "../../../c/k6/k6_mod.h"
#include "../../../c/k6/mod/sotto_tx_codec_io.h"
int k6_main(const k6_api_t *a) {
    sotto_io_t io;
    if (!a || a->abi!=1 || a->level!=31) return -2;
    if (sio_init(&io,a)) return -20;
    if (io.fault || io.deck!=0x110000u) return -21;
    return 7;
}
