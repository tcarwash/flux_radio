"""CLI options shared by the headless and browser emulator."""

def add_rf_args(parser):
    parser.add_argument("--rf-source", help="opt-in RX-only IQ: synthetic, sigmf:PATH, npy:PATH, npy-u8:PATH or soapy:ARGS")
    parser.add_argument("--rf-rate", type=float, help="synthetic/SDR/NPY IQ sample rate in Hz")
    parser.add_argument("--rf-center", type=float, help="initial IQ center Hz (required for NPY)")
    parser.add_argument("--rf-gain", type=float, help="SDR RX gain in dB")
    parser.add_argument("--rf-dbm-offset", type=float, help="full-scale dBm reference")
    parser.add_argument("--rf-loop", action="store_true", help="loop a recording instead of playing once")


def rf_options(args):
    return dict(rate=args.rf_rate, center_hz=args.rf_center, gain=args.rf_gain,
                dbm_offset=args.rf_dbm_offset, loop=args.rf_loop)
