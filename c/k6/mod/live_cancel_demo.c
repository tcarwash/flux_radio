/* Cooperative live cancellation smoke test. RX-only, no SPI writes.
 * One API-25 callback poll per 10 ms; return from the module on stop.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 25
#include "k6_mod.h"

int k6_main(const k6_api_t *api) {
    if (!api || api->abi != KM_ABI || api->level < 25) return -100;
    api->say("COOP: started");
    for (unsigned i = 0; i < 300; ++i) {
        int stop = api->cancel_poll();
        if (stop == 1) {
            api->say("COOP: cancelled");
            return -125;
        }
        if (stop == -1) return -101;
        if (api->key_down() || km_power_switch_off()) return -126;
        api->delay_us(10000);
    }
    api->say("COOP: completed");
    return 42;
}
