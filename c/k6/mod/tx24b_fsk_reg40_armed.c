/* Strictly opt-in TX24B conducted-dummy-load finite native-FSK + REG40
 * 8-frame research module, frozen before any physical RF experiment.
 * No per-symbol gain control and no TX26 4FSK vector overlap.
 * An independent GitHub queue lease ACK and canonical process serial
 * lock are mandatory before any radio/SDR access. No retries.
 */
#define K6_MODULE_LEVEL 3
#define TX24B_BENCH_ENABLE 1
#define TX24B_RF_ENABLE 1
#define TX24B_FIXTURE_ACK 1
#define TX24B_WATCHDOG_ACK 1
#define TX24B_HZ 433500000u
#include "tx24b_fsk_reg40_probe.c"
