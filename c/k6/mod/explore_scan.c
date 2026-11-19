/* EXPLORE: a sideloadable scan-and-rank module for Aura's module host.
   The host supplies the original two-stage scanner and candidate grader from firmware flash;
   this module keeps the UI and all live scan state in the module arena. Receive only.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 21
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "../cwid.h"
#include "../tonedet.h"
#define XS_COLS 1 /* no waterfall in the compact UI; keep just the scanner's required row byte */
#define XS_HOT 4 /* keep scanner state small enough for the shared decoder working set */
#include "../xscan.h"

K6_MODULE_NAME("EXPLORE");
K6_ACTION(K6_ACTION_EXPLORE_OPEN);

#define EX_CANDIDATES 7
#define BAND_COUNT 3
#define GPIOB 0x48000400u
#define GPIOC 0x48000800u
#define GPIOF 0x48001400u
#define IDR 0x10u
#define ODR 0x14u
#define BSRR 0x18u
#define BRR 0x28u
#define KEYPAD_ROWS ((1u << 0) | (1u << 4) | (1u << 9) | (1u << 10) | (1u << 11))
#define SPEAKER (1u << 2)

static const uint8_t TRY_SQUELCH_REGS[4] = {0x4D, 0x4E, 0x4F, 0x78};

typedef struct {
  xs_t scan;
  ex_t candidates;
  ex_cand_t entries[EX_CANDIDATES];
  /* Scanner is parked during Try. Reuse its 401-byte floor buffer for the CW decoder state;
     xs_resume rereads the segment floor before the scan judges bins again. */
  union {
    uint8_t scan_mem[401];
    cw_t cw;
  } work;
  uint32_t now, last_flush, parked_hz;
  uint8_t band, part, part_sweeps, list, selected, stable_key, eyes_right;
  uint8_t ab_down, ab_fired;
  uint32_t ab_since;
  char line[24];
  km_explore_frame_t frame;
} explore_t;

typedef struct {
  uint32_t lo, step, count;
  uint16_t snap;
} segment_t;

typedef struct {
  char name[6];
  uint8_t nseg;
  segment_t seg[3];
} band_t;

static const band_t BANDS[] = {
    {"ALL", 3, {{144000000u, 10000u, 400u, 5000u},
                 {223800000u, 20000u, 60u, 20000u},
                 {440000000u, 12500u, 400u, 12500u}}},
    {"2M", 1, {{144000000u, 10000u, 400u, 5000u}}},
    {"70CM", 2, {{440000000u, 12500u, 400u, 12500u},
                  {445000000u, 12500u, 400u, 12500u}}},
    {"1.25M", 1, {{222000000u, 20000u, 150u, 20000u}}},
};

static const uint8_t KEYS[24] = {
    0, 'A', 0, 'B', '7', '8', '#', '9', '4', '5', '0', '6',
    '1', '2', '*', '3', 'M', 'U', 'X', 'D', 'V', 'W', 0, 'N'};
static const uint8_t ROWPIN[6] = {0xFF, 0, 4, 10, 11, 9};

/* Add a new record without erasing or editing existing entries. Keep one wholly erased sector
   as the table's recovery reserve; when the other sectors fill, refuse safely and leave the
   journal untouched for the full table manager. */
#ifdef K6_EXPLORE_ENABLE_TABLE_SAVE
static int save_candidate(const k6_api_t *api, const ex_cand_t *c) {
  rt_rec_t rec;
  uint32_t addr, max_wseq = 0;
  uint16_t max_id = 0;
  unsigned sector, slot, spare = RT_SECTORS, append = RT_SLOTS;
  for (sector = 0; sector < RT_SECTORS; sector++) {
    int all_blank = 1;
    unsigned first_blank = RT_SLOTS;
    for (slot = 0; slot < RT_SLOTS_PER_SECTOR; slot++) {
      uint32_t index = sector * RT_SLOTS_PER_SECTOR + slot;
      uint32_t delta;
      addr = RT_BASE + index * RT_REC;
      if (api->fl_read(addr, &rec, sizeof rec) < 0) return -1;
      if (blank(&rec)) {
        if (first_blank == RT_SLOTS) first_blank = index;
        continue;
      }
      all_blank = 0;
      if (!rt_known_state(rec.state) ||
          ((rec.state & 0xFFu) == RT_ST_FREE) ||
          ((rec.state & 0xFFu) == RT_ST_VALID && !good(&rec))) return -2;
      if (rec.crc == rt_crc(&rec) && rec.id && rec.id != 0xFFFF && rec.id > max_id)
        max_id = rec.id;
      if (rec.crc == rt_crc(&rec) && (int32_t)(rec.wseq - max_wseq) > 0)
        max_wseq = rec.wseq;
      if (good(&rec) && !(rec.flags & RT_F_VFO)) {
        delta = rec.rx_hz > c->hz ? rec.rx_hz - c->hz : c->hz - rec.rx_hz;
        if (delta <= 5000u) return -4;
      }
    }
    if (all_blank) spare = sector;
    else if (append == RT_SLOTS && first_blank != RT_SLOTS)
      append = first_blank;
  }
  if (spare == RT_SECTORS || append == RT_SLOTS || max_id >= 0xFFFE) return -3;
  addr = RT_BASE + append * RT_REC;
  ex_to_rt(c, &rec);
  rec.state = 0xFFFFu;
  rec.seq = 1;
  rec.id = (uint16_t)(max_id + 1u);
  rec.wseq = max_wseq + 1u;
  rec.spare0 = 0;
  rec.crc = rt_crc(&rec);
  {
    uint8_t state[2] = {0xFEu, 0xFFu};
    if (api->fl_prog(addr, state, 2) < 0 ||
        api->fl_prog(addr + 2, (const uint8_t *)&rec + 2, RT_REC - 2) < 0)
      return -1;
    state[0] = 0xFCu;
    if (api->fl_prog(addr, state, 2) < 0) return -1;
  }
  return (int)append;
}
#endif

static uint32_t mmio_read(uint32_t addr) { return *(volatile uint32_t *)addr; }
static void mmio_write(uint32_t addr, uint32_t value) { *(volatile uint32_t *)addr = value; }

/* Tiny local divider for UI formatting; the large scanner and ranking engines live in host flash. */
static uint32_t udiv32(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  if (!d) return 0;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

static int keypad_scan(const k6_api_t *api) {
  int phase, key = 0;
  for (phase = 0; phase < 6 && !key; phase++) {
    uint32_t col;
    mmio_write(GPIOB + BSRR, KEYPAD_ROWS);
    if (phase) mmio_write(GPIOB + BRR, 1u << ROWPIN[phase]);
    api->delay_us(5);
    if (!(mmio_read(GPIOC + IDR) & (1u << 13))) col = 0;
    else if (!(mmio_read(GPIOC + IDR) & (1u << 14))) col = 1;
    else if (!(mmio_read(GPIOF + IDR) & (1u << 6))) col = 2;
    else if (!(mmio_read(GPIOB + IDR) & (1u << 14))) col = 3;
    else continue;
    key = KEYS[4 * phase + col];
  }
  mmio_write(GPIOB + BSRR, KEYPAD_ROWS);
  return key;
}

#define EXPLORE_AB_HOLD_MS 600u

/* Surf-style A/B hold: report 'w' once after 600 ms, never on a short press.
   The existing side-B ('B') handoff remains independent. */
static int explore_ab_event(explore_t *s, int key, uint32_t now) {
  if (key == 'W') {
    if (!s->ab_down) {
      s->ab_down = 1;
      s->ab_fired = 0;
      s->ab_since = now;
    } else if (!s->ab_fired && (uint32_t)(now - s->ab_since) >= EXPLORE_AB_HOLD_MS) {
      s->ab_fired = 1;
      return 'w';
    }
  } else if (s->ab_down) {
    s->ab_down = s->ab_fired = 0;
  }
  return 0;
}

/* Accept non-A/B keys on their press edge, and a long A/B hold once. */
static int keypad_event(explore_t *s, const k6_api_t *api) {
  int key = keypad_scan(api);
  int held = explore_ab_event(s, key, api->ms());
  if (key != s->stable_key) {
    s->stable_key = (uint8_t)key;
    if (key != 'W') return key;
  }
  return held;
}

static char *append(char *p, const char *text) {
  while (*text) *p++ = *text++;
  return p;
}

static char *decimal(char *p, uint32_t value, unsigned min_digits) {
  char tmp[10];
  unsigned n = 0;
  do {
    uint32_t q = udiv32(value, 10u);
    tmp[n++] = (char)('0' + value - q * 10u);
    value = q;
  } while (value || n < min_digits);
  while (n) *p++ = tmp[--n];
  return p;
}

static void line_end(char *p) { *p = 0; }

static void zero_bytes(void *mem, unsigned bytes) {
  uint8_t *p = (uint8_t *)mem;
  while (bytes--) *p++ = 0;
}

/* Keep this early diagnostic while the compact host is being validated. It reports the actual
   scratch extent the module receives and the complete state block it needs, before rejecting. */
static void report_scratch(const k6_api_t *api, uint32_t have, uint32_t need) {
  char line[32];
  char *p = append(line, "EXP RAM ");
  p = decimal(p, have, 1);
  p = append(p, " need ");
  p = decimal(p, need, 1);
  line_end(p);
  api->say(line);
}

static void screen_frame(explore_t *s, const k6_api_t *api, unsigned view) {
  unsigned i, total, first;
  ex_cand_t *c;
  if (api->level < 21 || !api->lcd_explore_frame) return;
  zero_bytes(&s->frame, sizeof s->frame);
  s->frame.view = (uint8_t)view;
  s->frame.band = s->band;
  s->frame.gaze = s->eyes_right;
  first = s->selected > KM_EXPLORE_ROWS - 1 ? s->selected - (KM_EXPLORE_ROWS - 1) : 0;
  s->frame.selected = (uint8_t)(s->selected - first);
  s->frame.found = total = api->explore_ex_view_n(&s->candidates);
  s->frame.sweeps = s->scan.sweeps;
  s->frame.scan_hz = view == 2 ? s->parked_hz : api->rx_hz();
  s->frame.level = (int16_t)(((api->fd_read(0x67) >> 1) & 0x1ffu) - 174);
  for (i = 0; i < KM_EXPLORE_ROWS && first + i < total; i++) {
    km_explore_row_t *r = &s->frame.rows[i];
    c = (ex_cand_t *)api->explore_ex_view(&s->candidates, first + i);
    if (!c) break;
    r->hz = c->hz; r->off_hz = c->off_hz; r->overs = c->overs;
    r->tone = c->tone; r->hi = c->hi; r->score = c->score;
    r->flags = c->flags; r->grade = (uint8_t)c->grade;
    for (unsigned j = 0; j < sizeof r->call; j++) r->call[j] = c->call[j];
    s->frame.count++;
  }
  api->lcd_explore_frame(&s->frame);
  api->lcd_flush();
  s->last_flush = api->ms();
}

static void screen_scan(explore_t *s, const k6_api_t *api) { screen_frame(s, api, 0); }
static void screen_list(explore_t *s, const k6_api_t *api) { screen_frame(s, api, 1); }

static int start_band(explore_t *s, const k6_api_t *api, unsigned mode, unsigned part) {
  unsigned i;
  const band_t *b = &BANDS[mode];
  unsigned n = 1;
  if (part >= b->nseg) part = 0;
  api->explore_xs_defaults(&s->scan);
  s->scan.nseg = (uint8_t)n;
  /* All configured ranges already use their intended channel raster. Skip the optional
     31-sample frequency refinement to keep the module's 1,152-byte execution stack generous. */
  s->scan.refine = 0;
  s->scan.quiet = 1;
  s->scan.say_rows = 0;
  s->scan.peek_ms = 0;
  for (i = 0; i < n; i++) {
    const segment_t *g = &b->seg[part + i];
    s->scan.seg[i].lo_hz = g->lo;
    s->scan.seg[i].step_hz = g->step;
    s->scan.seg[i].nbins = (uint16_t)g->count;
    s->scan.seg[i].snap_hz = g->snap;
  }
  s->band = (uint8_t)mode, s->part = (uint8_t)part;
  return api->explore_xs_init(&s->scan, api, &s->candidates,
                              s->work.scan_mem, sizeof s->work.scan_mem);
}

/* Park on a candidate while retaining the scan state. Use the host's shared CW and sub-audio
   decoders to identify its repeater ID/tone during the manual listen; xs_resume refreshes the
   segment floor before judging further bins. */
static void try_frequency(explore_t *s, const k6_api_t *api, ex_cand_t *c) {
  char last_call[11] = {0};
  uint8_t last_flags = 0, same = 0, confirmed = 0;
  uint32_t found_tone = 0, last_draw = 0;
  int tone_active;
  cw_t *cw = &s->work.cw;
  int i;
  uint16_t saved_af = api->fd_read(0x47), saved_sq[4];
  uint32_t saved_speaker = mmio_read(GPIOB + ODR) & SPEAKER;
  for (i = 0; i < 4; i++) saved_sq[i] = api->fd_read(TRY_SQUELCH_REGS[i]);
  /* tune() only retunes RX. Try owns the audio path and opens squelch so a weak
     candidate can be heard; keep the amplifier muted until the PLL has settled. */
  mmio_write(GPIOB + BRR, SPEAKER);
  api->fd_write(0x47, 0x6040);
  s->parked_hz = c->hz;
  api->tune(c->hz, 1);
  api->fd_write(0x4D, (uint16_t)((saved_sq[0] & 0xFF00u) | 0xFFu));
  api->fd_write(0x4E, (uint16_t)((saved_sq[1] & 0xFF00u) | 0xFFu));
  api->fd_write(0x4F, 0x7F7F);
  api->fd_write(0x78, 0);
  c->flags |= EX_F_TRIED;
  api->cw_init(cw);
  td_on(api);
  tone_active = api->tone_start() == 0;
  api->delay_us(12000);
  api->fd_write(0x47, 0x6140); /* FM receive audio, as in SURF monitor */
  mmio_write(GPIOB + BSRR, SPEAKER);
  last_draw = api->ms();
  api->say("EXPLORE Try; key to resume scan");
  screen_frame(s, api, 2);
  /* Let the key that opened Try be released before accepting its return key. */
  while (!km_power_switch_off() && keypad_event(s, api) != 0) api->delay_us(10000);
  while (!km_power_switch_off()) {
    if (keypad_event(s, api) != 0) break;
    {
      uint32_t now = api->ms();
      uint16_t b = td_read(api);
      int keyed = (b & TD_ON) != 0;
      if (now - last_draw >= 250u) {
        screen_frame(s, api, 2);
        last_draw = now;
      }
      if (tone_active) {
        int tone = api->tone_step();
        if (tone > 0 && (uint32_t)tone != found_tone) {
          found_tone = (uint32_t)tone;
          api->explore_ex_tone(&s->candidates, c->hz, (uint16_t)tone);
          api->say("EXPLORE repeater tone found");
        }
      }
      if (api->cw_feed(cw, now, keyed) && cw->nd) {
        char call[11] = {0};
        uint8_t flags = 0;
        if (api->cw_call(cw->text, call, &flags)) {
          for (i = 0; i < 11 && call[i] == last_call[i] && call[i]; i++) {}
          if (i == 11 || (call[i] == last_call[i] && flags == last_flags)) {
            if (same < 255) same++;
          } else {
            for (i = 0; i < 11; i++) last_call[i] = call[i];
            last_flags = flags;
            same = 1;
          }
          if (same >= 2 && !confirmed) {
            api->explore_ex_cw(&s->candidates, c->hz, call);
            confirmed = 1;
            api->say("EXPLORE CW ID confirmed");
            screen_frame(s, api, 2);
          }
        }
      }
      api->delay_us(2000);
    }
  }
  mmio_write(GPIOB + BRR, SPEAKER);
  api->fd_write(0x47, 0x6040);
  if (tone_active) api->tone_stop();
  td_off(api);
  for (i = 0; i < 4; i++) api->fd_write(TRY_SQUELCH_REGS[i], saved_sq[i]);
  api->fd_write(0x47, saved_af);
  mmio_write(GPIOB + (saved_speaker ? BSRR : BRR), SPEAKER);
  api->explore_xs_resume(&s->scan);
  api->say("EXPLORE scan resumed");
  if (s->list) screen_list(s, api);
  else screen_scan(s, api);
}

int k6_main(const k6_api_t *api) {
  explore_t *s;
  uint32_t bytes;
  uint8_t *mem;
  int result, key;
  if (api->level < 21) return -3;
  mem = api->scratch(&bytes);
  report_scratch(api, bytes, sizeof(explore_t));
  if (!mem || bytes < sizeof(explore_t)) return -8;
  s = (explore_t *)mem;
  for (uint32_t i = 0; i < sizeof *s; i++) ((uint8_t *)s)[i] = 0;
  api->explore_ex_init(&s->candidates, s->entries, EX_CANDIDATES);
#ifdef K6_EXPLORE_SIM_FIXTURE
  /* Deterministic UI fixture only: the simulator has no RF stimulus source. */
  s->entries[0].hz = 146940000u;
  s->entries[0].overs = 3;
  s->entries[0].lo = -82;
  s->entries[0].hi = -80;
  s->entries[0].grade = 'R';
  s->entries[0].score = 8;
  s->entries[0].last_ms = api->ms();
  s->candidates.n = 1;
#endif
  api->say("EXPLORE scan ready");
  result = start_band(s, api, 0, 0);
  if (result) return result;
  api->say("EXPLORE band ALL");
  api->say("EXPLORE scan/list started; RX only");
  screen_scan(s, api);
  while (!km_power_switch_off()) {
    result = api->explore_xs_step(&s->scan);
    if (result == XS_ROW) {
      s->eyes_right ^= 1u;
      s->now = api->ms();
      if (s->list) screen_list(s, api);
      else if (s->now - s->last_flush >= 250) screen_scan(s, api);
      /* The built-in Explore borrows a large multi-band floor table. A sideload cannot, so
         rotate the same band-set segments one sweep at a time and reuse one 400-bin buffer. */
      /* xs_init() clears the per-bin floor table. xscan intentionally uses its first sweep
         only to seed those floors; carrier confirmation starts on the next sweep. Keep each
         range alive for several sweeps before rotating, or ALL/70CM would perpetually restart
         at sweeps=0 and never report a candidate. */
      if ((s->band == 0 || s->band == 2) && ++s->part_sweeps >= 3) {
        s->part_sweeps = 0;
        unsigned parts = BANDS[s->band].nseg;
        unsigned next_part = (unsigned)s->part + 1u;
        if (next_part >= parts) next_part = 0;
        api->explore_xs_close(&s->scan);
        result = start_band(s, api, s->band, next_part);
        if (result) break;
      }
    }
    key = keypad_event(s, api);
    if (key == 'w' && !s->list && api->level >= 22) api->action_chirp(1);
    if (key == 'X') break;
    if (key == 'M') {
      s->list = (uint8_t)!s->list;
      api->say(s->list ? "EXPLORE candidate list" : "EXPLORE band scan");
      if (s->list) api->say("EXPLORE A try; * ignore; saved list limited");
      if (s->list) screen_list(s, api);
      else screen_scan(s, api);
    } else if (key == '#' || key == 'N') {
      api->explore_xs_close(&s->scan);
      unsigned next_band = (unsigned)s->band + 1u;
      if (next_band > BAND_COUNT) next_band = 0;
      result = start_band(s, api, next_band, 0);
      if (result) break;
      api->say(next_band == 0 ? "EXPLORE band ALL" :
               next_band == 1 ? "EXPLORE band 2M" :
               next_band == 2 ? "EXPLORE band 70CM" : "EXPLORE band 1.25M");
      if (s->list) screen_list(s, api);
      else screen_scan(s, api);
    } else if (s->list && key == 'U' && s->selected) {
      s->selected--;
      screen_list(s, api);
    } else if (s->list && key == 'D' &&
               s->selected + 1 < api->explore_ex_view_n(&s->candidates)) {
      s->selected++;
      screen_list(s, api);
    } else if (s->list && key == 'A') {
      ex_cand_t *c = (ex_cand_t *)api->explore_ex_view(&s->candidates, s->selected);
      if (c) try_frequency(s, api, c);
      else api->say("EXPLORE select a candidate first");
    } else if (s->list && (key == 'B' || key == 'w')) {
      ex_cand_t *c = (ex_cand_t *)api->explore_ex_view(&s->candidates, s->selected);
      if (key == 'w' && api->level >= 22) api->action_chirp(c ? 0 : 1);
      if (c && api->vfo_b_tune(c->hz, 0) == 0) return 0;
      api->say(c ? "EXPLORE VFO handoff failed" : "EXPLORE select a candidate first");
    }
    else if (s->list && key == '*') {
      ex_cand_t *c = (ex_cand_t *)api->explore_ex_view(&s->candidates, s->selected);
      if (c) {
        c->flags ^= EX_F_IGNORED;
        api->say((c->flags & EX_F_IGNORED) ? "EXPLORE ignored" : "EXPLORE unignored");
        screen_list(s, api);
      }
    }
#ifdef K6_EXPLORE_ENABLE_TABLE_SAVE
    else if (s->list && key == '5') {
      ex_cand_t *c = (ex_cand_t *)api->explore_ex_view(&s->candidates, s->selected);
      if (!c) api->say("EXPLORE select a candidate first");
      else if (!api->fl_prog || !api->fl_read) api->say("EXPLORE table unavailable");
      else if (c->flags & EX_F_KNOWN)
        api->say("EXPLORE candidate already saved");
      else {
        result = save_candidate(api, c);
        if (result >= 0) {
          ex_know(&s->candidates, c->hz);
          api->say("EXPLORE candidate saved to table");
          screen_list(s, api);
        } else if (result == -2) api->say("EXPLORE table not valid");
        else if (result == -4) api->say("EXPLORE candidate already saved");
        else if (result == -3) api->say("EXPLORE table full");
        else api->say("EXPLORE table save failed");
      }
    }
#else
    else if (s->list && key == '5') {
      api->say("EXPLORE save unavailable: module RAM limit");
    }
#endif
  }
  api->explore_xs_close(&s->scan);
  if (api->level >= 6) {
    api->lcd_text(0, "EXPLORE stopped");
    api->lcd_text(1, "MENU opens list");
    api->lcd_text(5, "Exit returns to Aura");
    api->lcd_flush();
  }
  return (int)s->scan.sweeps;
}
