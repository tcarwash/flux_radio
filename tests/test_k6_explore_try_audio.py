"""Run EXPLORE's real Try handler on a Cortex-M0 with receiver/keypad callbacks."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_k6_i2s import ROOT, START, need_clang, run
from modem import k6mod


HARNESS = r'''
#define k6_main unused_explore_main
#include "SOURCE"
#undef k6_main
static void init_cw(void *p) { zero_bytes(p, sizeof(cw_t)); }
static int feed_cw(void *p, uint32_t ms, int keyed) { (void)p; (void)ms; (void)keyed; return 0; }
static int start_tone(void) { return 0; }
static int step_tone(void) { return 0; }
static void stop_tone(void) {}
static void resume_scan(void *p) { (void)p; }
int k6_main(const k6_api_t *base) {
  k6_api_t api;
  explore_t s;
  ex_cand_t candidate;
  zero_bytes(&api, sizeof api);
  zero_bytes(&s, sizeof s);
  zero_bytes(&candidate, sizeof candidate);
  api.level = 21;
  api.say = base->say;
  api.fd_read = base->fd_read;
  api.fd_write = base->fd_write;
  api.delay_us = base->delay_us;
  api.tune = base->tune;
  api.ms = base->ms;
  api.cw_init = init_cw;
  api.cw_feed = feed_cw;
  api.tone_start = start_tone;
  api.tone_step = step_tone;
  api.tone_stop = stop_tone;
  api.explore_xs_resume = resume_scan;
  candidate.hz = 444475000u;
  try_frequency(&s, &api, &candidate);
  return (candidate.flags & EX_F_TRIED) != 0;
}
'''


@need_clang
@pytest.mark.parametrize('power_off', [False, True])
@pytest.mark.parametrize('saved_af', [0x6040, 0x6740])
def test_try_opens_audio_and_restores_receiver(tmp_path, power_off, saved_af):
    source = tmp_path / 'try.c'
    source.write_text(HARNESS.replace('SOURCE', str(Path(ROOT) / 'c/k6/mod/explore_scan.c')))
    code = k6mod.build(str(source))
    initial = {**START, 0x47: saved_af, 0x4D: 0xA123, 0x4E: 0xB234,
               0x4F: 0x3456, 0x78: 0x4567}
    snapshots, tunes = [], []
    def capture_gpio(off, value, state):
        assert off in (0x18, 0x28)
        set_bits = value & 0xFFFF if off == 0x18 else 0
        clear_bits = value >> 16 if off == 0x18 else value & 0xFFFF
        state['odr'] = (state['odr'] | set_bits) & ~clear_bits
        if value & 4:
            snapshots.append((off, dict(state['regs'])))
    lcd = {}
    ret, said, final, writes = run(initial, code=code, level=21,
        keypad=[] if power_off else [(200000, 300000, 'X')],
        power_off_us=200000 if power_off else None,
        gpiob=capture_gpio, lcd=lcd, count=2000000,
        tune=lambda hz, wide, us: tunes.append((hz, wide)))
    assert ret == 1 and tunes == [(444475000, 1)]
    opened = [regs for off, regs in snapshots if off == 0x18]
    assert opened, 'Try never enabled the speaker amplifier'
    assert opened[0][0x47] == 0x6140
    assert opened[0][0x4D] == 0xA1FF and opened[0][0x4E] == 0xB2FF
    assert opened[0][0x4F] == 0x7F7F and opened[0][0x78] == 0
    assert not lcd['st']['odr'] & 4
    assert all(final[reg] == value for reg, value in initial.items())
    assert said[-1] == 'EXPLORE scan resumed'
    assert all(reg not in (0x30, 0x33, 0x36, 0x37) for reg, value in writes)
