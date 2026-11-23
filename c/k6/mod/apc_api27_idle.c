/* API27 offline refusal probe: never keys TX, never modifies SPI.
 * This is for firmware emulator / read-only API ABI verification only. */
#define K6_MODULE_LEVEL 27
#include "../k6_mod.h"
int k6_main(const k6_api_t *api){
    if (!api || api->abi != 1 || api->level < 27) return -3;
    if (!api->apc_set) return -4;
    if (api->apc_set(100u) != -1) return -5;
    if (api->apc_set(99u) != -1) return -6;
    if (api->apc_set(70u) != -1) return -7;
    api->say("APC-27 idle refuses; no TX");
    return 27;
}
