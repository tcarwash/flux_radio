/* TX24 strictly opt-in one-shot 433.500 MHz conducted dummy-load source.
 * This wrapper must match the frozen SHA256 in the physical bench.
 * NEVER transmit without a separately acknowledged shared bench lease
 * and canonical nonblocking k6_port_lock.serial_lock.
 */
#define K6_MODULE_LEVEL 3
#define TX24_BENCH_ENABLE 1
#define TX24_RF_ENABLE 1
#define TX24_FIXTURE_ACK 1
#define TX24_WATCHDOG_ACK 1
#define TX24_FREQ_HZ 433500000u
#include "tx24_reg40_tone_probe.c"
