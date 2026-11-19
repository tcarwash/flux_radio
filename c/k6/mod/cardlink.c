/* CARD LINK: send or receive lossless PixelQSO PXK6 records over FD6818B configurable native FSK.
   The module is half duplex and uses per-frame stop-and-wait acknowledgements. */
#define K6_MODULE_LEVEL 24
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "../card_air.h"
#include "../card_record.h"
#include "k6ui.h"

K6_MODULE_NAME("CARD LINK");

/* The module runtime intentionally has no libgcc division helper. */
uint32_t __aeabi_uidiv(uint32_t numerator, uint32_t denominator) {
  return ui_udiv(numerator, denominator);
}

#define STORE_BASE 0x190000u
#define STORE_END 0x200000u
#define STORE_SECTOR 4096u
#define STORE_SLOTS ((STORE_END - STORE_BASE) / STORE_SECTOR)
#define CARD_FSK_CONFIG (1200u | (78u << 16))
#define AIR_TIMEOUT_MS 3500u
#define FRAME_RETRIES 3u
#define LCD_ROWS 30u
#define LCD_BYTES (16u * LCD_ROWS)
#define ERR_CANCEL (-10)
#define ERR_NO_CARD (-11)
#define ERR_TX (-12)
#define ERR_STOP (-13)
#define ERR_RAM (-14)
#define ERR_UI (-15)

/* The module linker has no C runtime. Give the included portable codec its own byte helpers. */
static void *cardlink_memcpy(void *dst, const void *src, size_t n) {
  uint8_t *d = (uint8_t *)dst;
  const uint8_t *s = (const uint8_t *)src;
  while (n--) *d++ = *s++;
  return dst;
}
static void *cardlink_memset(void *dst, int value, size_t n) {
  uint8_t *d = (uint8_t *)dst;
  while (n--) *d++ = (uint8_t)value;
  return dst;
}
static int cardlink_memcmp(const void *a, const void *b, size_t n) {
  const uint8_t *x = (const uint8_t *)a, *y = (const uint8_t *)b;
  while (n--) { if (*x != *y) return (int)*x - (int)*y; ++x; ++y; }
  return 0;
}
#define memcpy cardlink_memcpy
#define memset cardlink_memset
#define memcmp cardlink_memcmp
#define K6_MODULE_NO_LIBC 1
#include "../card_air.c"
#include "../card_record.c"
#undef K6_MODULE_NO_LIBC
#undef be16

static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (uint16_t)p[1] << 8); }
static uint32_t le32(const uint8_t *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static uint16_t be16_mod(const uint8_t *p) { return (uint16_t)((uint16_t)p[0] << 8 | p[1]); }

static int user_stop(const k6_api_t *api, ui_t *ui) {
  return ui->on ? ui_key(ui) != 0 : api->key_down();
}

static void show_frequency(ui_t *ui, const k6_api_t *api, const char *mode) {
  char line[UI_COLS + 1], *p;
  p = ui_put(line, mode);
  *p++ = ' ';
  p = ui_mhz(p, api->rx_hz());
  *p = 0;
  ui_text(ui, 1, line);
  ui_now(ui);
}

static void raw_to_words(const uint8_t raw[K6_CA_FRAME_BYTES], uint16_t words[8]) {
  unsigned i;
  /* FD6818B writes a 16-bit FIFO register high byte first; this byte order is still pending
     confirmation from a real K6-to-K6 capture. The receiver accepts either word byte order. */
  for (i = 0; i < 8; ++i) words[i] = (uint16_t)((uint16_t)raw[2 * i] << 8 | raw[2 * i + 1]);
}

static int words_to_raw(const uint16_t words[8], uint8_t raw[K6_CA_FRAME_BYTES]) {
  uint8_t swapped[K6_CA_FRAME_BYTES];
  unsigned i;
  k6_card_air_frame_t frame;
  for (i = 0; i < 8; ++i) {
    raw[2 * i] = (uint8_t)(words[i] >> 8);
    raw[2 * i + 1] = (uint8_t)words[i];
    swapped[2 * i] = (uint8_t)words[i];
    swapped[2 * i + 1] = (uint8_t)(words[i] >> 8);
  }
  if (k6_card_air_decode(raw, &frame) == 0) return 0;
  if (k6_card_air_decode(swapped, &frame) == 0) {
    memcpy(raw, swapped, K6_CA_FRAME_BYTES);
    return 0;
  }
  return -1;
}

static int radio_send(const k6_api_t *api, const uint8_t raw[K6_CA_FRAME_BYTES]) {
  uint16_t words[8];
  int r;
  api->fsk_rx_stop();
  r = api->tx_on(1800);
  if (r < 0) return r;
  raw_to_words(raw, words);
  r = api->fsk_configured(2, words, 8, CARD_FSK_CONFIG);
  api->tx_off();
  return r;
}

static int radio_receive(const k6_api_t *api, ui_t *ui, uint8_t raw[K6_CA_FRAME_BYTES],
                         uint32_t timeout_ms) {
  uint16_t words[8];
  uint32_t start = api->ms();
  if (api->fsk_configured(0, 0, 8, CARD_FSK_CONFIG) < 0) return -2;
  while ((uint32_t)(api->ms() - start) < timeout_ms) {
    if (user_stop(api, ui)) {
      api->fsk_rx_stop();
      return -3;
    }
    if (api->fsk_configured(1, words, 8, CARD_FSK_CONFIG) == 8) {
      if (words_to_raw(words, raw) == 0) return 0;
    }
    api->delay_us(2000);
  }
  api->fsk_rx_stop();
  return -1;
}

static int send_control(const k6_api_t *api, uint8_t kind, uint16_t transfer_id,
                        uint16_t next_sequence) {
  k6_card_air_frame_t f;
  uint8_t raw[K6_CA_FRAME_BYTES];
  memset(&f, 0, sizeof f);
  f.kind = kind;
  f.transfer_id = transfer_id;
  f.sequence = next_sequence;
  if (k6_card_air_encode(&f, raw) < 0) return -1;
  return radio_send(api, raw);
}

/* Send one stop-and-wait frame. Lost ACKs are safe: the receiver recognizes duplicates and
   answers with its current next-sequence value. */
static int send_and_wait(const k6_api_t *api, ui_t *ui, const uint8_t raw[K6_CA_FRAME_BYTES],
                         uint16_t transfer_id, uint16_t next_sequence) {
  unsigned attempt;
  uint8_t reply[K6_CA_FRAME_BYTES];
  k6_card_air_frame_t ack;
  for (attempt = 0; attempt < FRAME_RETRIES; ++attempt) {
    if (user_stop(api, ui)) return -3;
    if (radio_send(api, raw) < 0) continue;
    if (radio_receive(api, ui, reply, AIR_TIMEOUT_MS) < 0) continue;
    if (k6_card_air_decode(reply, &ack) < 0 || ack.transfer_id != transfer_id) continue;
    if (ack.kind == K6_CA_ACK && ack.sequence == next_sequence) return 0;
    if (ack.kind == K6_CA_NAK && ack.sequence == next_sequence - 1) continue;
  }
  return -1;
}

static int encode_data_frame(uint16_t transfer_id, uint16_t sequence,
                             const uint8_t *data, size_t count,
                             uint8_t raw[K6_CA_FRAME_BYTES]) {
  k6_card_air_frame_t f;
  unsigned i;
  memset(&f, 0, sizeof f);
  f.kind = K6_CA_DATA;
  f.transfer_id = transfer_id;
  f.sequence = sequence;
  for (i = 0; i < count; ++i) f.payload[i] = data[i];
  return k6_card_air_encode(&f, raw);
}

static int send_card(const k6_api_t *api, ui_t *ui, const uint8_t *record, size_t bytes,
                     const pxk6_card_t *card) {
  k6_card_air_frame_t f;
  uint8_t raw[K6_CA_FRAME_BYTES];
  uint32_t checksum = pxk6_crc32(record, bytes);
  uint16_t transfer_id = (uint16_t)(api->ms() ^ card->card_id ^ checksum);
  uint16_t seq = 1;
  size_t off;
  char line[UI_COLS + 1], *p;
  if (!transfer_id) transfer_id = 1;
  memset(&f, 0, sizeof f);
  f.kind = K6_CA_BEGIN;
  f.transfer_id = transfer_id;
  k6_card_air_record_meta(f.payload, (uint16_t)bytes, checksum);
  if (k6_card_air_encode(&f, raw) < 0) return -1;
  {
    int r = send_and_wait(api, ui, raw, transfer_id, seq);
    if (r < 0) return r;
  }
  show_frequency(ui, api, "SEND");
  for (off = 0; off < bytes; off += K6_CA_PAYLOAD_BYTES, ++seq) {
    size_t n = bytes - off < K6_CA_PAYLOAD_BYTES ? bytes - off : K6_CA_PAYLOAD_BYTES;
    if (encode_data_frame(transfer_id, seq, record + off, n, raw) < 0) return -1;
    {
      int r = send_and_wait(api, ui, raw, transfer_id, (uint16_t)(seq + 1));
      if (r < 0) return r;
    }
    p = ui_put(line, "DATA ");
    p = ui_dec(p, (uint32_t)(off + n), 4);
    *p++ = '/'; p = ui_dec(p, (uint32_t)bytes, 4); *p = 0;
    ui_text(ui, 2, line); ui_flush(ui, 250);
  }
  memset(&f, 0, sizeof f);
  f.kind = K6_CA_END;
  f.transfer_id = transfer_id;
  f.sequence = seq;
  k6_card_air_record_meta(f.payload, (uint16_t)bytes, checksum);
  if (k6_card_air_encode(&f, raw) < 0) return -1;
  {
    int r = send_and_wait(api, ui, raw, transfer_id, (uint16_t)(seq + 1));
    if (r < 0) return r;
  }
  return 0;
}

static void show_preview(const k6_api_t *api, ui_t *ui, const pxk6_card_t *card,
                         uint8_t *preview);

static int read_slot(const k6_api_t *api, unsigned slot, uint8_t *record, size_t capacity,
                     size_t *record_bytes, pxk6_card_t *card) {
  uint8_t header[PXK6_HEADER_BYTES];
  size_t bytes;
  uint32_t addr = STORE_BASE + slot * STORE_SECTOR;
  if (slot >= STORE_SLOTS || api->fl_read(addr, header, sizeof header) < 0 ||
      header[0] != 'P' || header[1] != 'X' || header[2] != 'K' || header[3] != '6' || header[4] != 1)
    return ERR_NO_CARD;
  bytes = PXK6_HEADER_BYTES + be16_mod(header + 14) + PXK6_CRC_BYTES;
  if (bytes > capacity || bytes > PXK6_MAX_RECORD_BYTES ||
      api->fl_read(addr, record, (uint32_t)bytes) < 0 ||
      pxk6_card_open(record, bytes, card) < 0) return ERR_NO_CARD;
  *record_bytes = bytes;
  return 0;
}

/* Browse the radio's non-executable PXK6 store. Up/down changes the live grayscale preview;
   1 selects the highlighted full-color record for sending. The card record is never altered. */
static int select_card(const k6_api_t *api, ui_t *ui, uint8_t *record, size_t capacity,
                       size_t *record_bytes, pxk6_card_t *card) {
  uint8_t slots[STORE_SLOTS], header[PXK6_HEADER_BYTES];
  uint8_t preview[LCD_BYTES];
  unsigned n = 0, slot, selected = 0;
  int key;
  char line[UI_COLS + 1], *p;
  if (!ui->on) return ERR_UI;
  for (slot = 0; slot < STORE_SLOTS; ++slot) {
    uint32_t addr = STORE_BASE + slot * STORE_SECTOR;
    if (api->fl_read(addr, header, sizeof header) == 0 &&
        header[0] == 'P' && header[1] == 'X' && header[2] == 'K' && header[3] == '6' &&
        header[4] == 1 && be16_mod(header + 14) <= PXK6_MAX_RECORD_BYTES)
      slots[n++] = (uint8_t)slot;
  }
  if (!n) return ERR_NO_CARD;
  ui_release(ui);
  for (;;) {
    size_t bytes = 0;
    if (read_slot(api, slots[selected], record, capacity, &bytes, card) < 0) {
      ui_text(ui, 1, "CARD READ ERROR"); ui_now(ui); return ERR_NO_CARD;
    }
    p = ui_put(line, "CARD "); p = ui_dec(p, selected + 1, 1); *p++ = '/';
    p = ui_dec(p, n, 1); *p = 0; ui_text(ui, 1, line);
    p = line;
    for (slot = 0; slot < card->callsign_bytes && (size_t)(p - line) < UI_COLS; ++slot)
      *p++ = (char)card->callsign[slot];
    if (card->grid_bytes && (size_t)(p - line) < UI_COLS) *p++ = ' ';
    for (slot = 0; slot < card->grid_bytes && (size_t)(p - line) < UI_COLS; ++slot)
      *p++ = (char)card->grid[slot];
    *p = 0; ui_text(ui, 2, line);
    p = ui_put(line, "ID "); p = ui_dec(p, card->card_id, 4); *p = 0; ui_text(ui, 3, line);
    ui_text(ui, 5, "U/D:PREVIEW 1:SEND X:BACK"); ui_now(ui);
    show_preview(api, ui, card, preview);
    p = ui_put(line, "CARD "); p = ui_dec(p, selected + 1, 1); *p++ = '/';
    p = ui_dec(p, n, 1); *p = 0; ui_text(ui, 1, line); ui_now(ui);
    do {
      key = ui_key(ui);
      if (!key) api->delay_us(10000);
    } while (!key);
    if (key == 'X') return ERR_CANCEL;
    if (key == 'U' || key == 'D') {
      if (key == 'U') selected = selected ? selected - 1 : n - 1;
      else selected = selected + 1 >= n ? 0 : selected + 1;
    } else if (key == '1') {
      *record_bytes = bytes;
      return 0;
    }
    api->delay_us(key ? 100000 : 10000);
  }
}

static int run_sender(const k6_api_t *api, ui_t *ui, uint8_t *scratch, size_t scratch_bytes) {
  size_t bytes = 0;
  pxk6_card_t card;
  int r = select_card(api, ui, scratch, scratch_bytes, &bytes, &card);
  if (r < 0) return r;
  ui_text(ui, 1, "Start peer RECEIVE");
  ui_text(ui, 2, "then press # here");
  ui_text(ui, 5, "#:SEND X:CANCEL"); ui_now(ui);
  for (;;) {
    int k = ui_key(ui);
    if (k == 'X') return ERR_CANCEL;
    if (k == '#') break;
    api->delay_us(10000);
  }
  r = send_card(api, ui, scratch, bytes, &card);
  if (r == -3) return ERR_CANCEL;
  if (r < 0) return ERR_TX;
  ui_text(ui, 1, "TRANSFER COMPLETE");
  ui_text(ui, 2, "Full color sent");
  ui_now(ui);
  return 0;
}

static int parse_meta(const uint8_t payload[K6_CA_PAYLOAD_BYTES], size_t *bytes, uint32_t *crc) {
  *bytes = le16(payload);
  *crc = le32(payload + 2);
  return payload[6] == 1 && payload[7] == 0 &&
         *bytes >= PXK6_HEADER_BYTES + PXK6_CRC_BYTES && *bytes <= PXK6_MAX_RECORD_BYTES;
}

static void show_preview(const k6_api_t *api, ui_t *ui, const pxk6_card_t *card,
                         uint8_t *preview) {
  char line[UI_COLS + 1];
  unsigned i, n = 0;
  if (pxk6_card_preview(card, preview, 128, LCD_ROWS) < 0) return;
  ui_text(ui, 0, "PIXELQSO CARD");
  for (i = 0; i < card->callsign_bytes && n < UI_COLS; ++i) line[n++] = (char)card->callsign[i];
  if (card->grid_bytes && n < UI_COLS) line[n++] = ' ';
  for (i = 0; i < card->grid_bytes && n < UI_COLS; ++i) line[n++] = (char)card->grid[i];
  line[n] = 0;
  ui_text(ui, 1, line);
  ui_text(ui, 5, "FULL CARD SAVED");
  api->lcd_wf(preview, LCD_ROWS, 0);
  api->lcd_flush();
}

static int run_receiver(const k6_api_t *api, ui_t *ui, uint8_t *scratch, size_t scratch_bytes) {
  size_t length = 0, received = 0;
  uint32_t expected_crc = 0, last_frame = api->ms(), success_at = 0;
  uint16_t transfer_id = 0, expected_seq = 0;
  uint8_t raw[K6_CA_FRAME_BYTES], *preview;
  int active = 0, stored = 0;
  pxk6_card_t card;
  char line[UI_COLS + 1], *p;
  if (scratch_bytes < PXK6_MAX_RECORD_BYTES + LCD_BYTES) return ERR_RAM;
  preview = scratch + PXK6_MAX_RECORD_BYTES;
  show_frequency(ui, api, "RECEIVE");
  ui_text(ui, 5, "Waiting; X stops"); ui_now(ui);
  for (;;) {
    k6_card_air_frame_t frame;
    size_t meta_len;
    uint32_t meta_crc;
    int rr = radio_receive(api, ui, raw, AIR_TIMEOUT_MS);
    if (rr == -3) return ERR_STOP;
    if (rr == -2) return ERR_TX;
    if (rr < 0) {
      if (stored && (uint32_t)(api->ms() - success_at) > 15000u) return 0;
      continue;
    }
    if (k6_card_air_decode(raw, &frame) < 0) continue;
    last_frame = api->ms();
    if (frame.kind == K6_CA_BEGIN && frame.sequence == 0 &&
        parse_meta(frame.payload, &meta_len, &meta_crc) &&
        scratch_bytes >= meta_len + LCD_BYTES) {
      length = meta_len; expected_crc = meta_crc; received = 0;
      transfer_id = frame.transfer_id; expected_seq = 1; active = 1; stored = 0;
      send_control(api, K6_CA_ACK, transfer_id, expected_seq);
      ui_text(ui, 2, "Receiving full card"); ui_now(ui);
      continue;
    }
    if (!active || frame.transfer_id != transfer_id) continue;
    if (frame.kind == K6_CA_DATA) {
      if (frame.sequence < expected_seq && frame.sequence > 0) {
        size_t offset = (size_t)(frame.sequence - 1) * K6_CA_PAYLOAD_BYTES;
        size_t count = length > offset ? length - offset : 0;
        if (count > K6_CA_PAYLOAD_BYTES) count = K6_CA_PAYLOAD_BYTES;
        if (count && memcmp(scratch + offset, frame.payload, count) == 0)
          send_control(api, K6_CA_ACK, transfer_id, expected_seq);
        else send_control(api, K6_CA_NAK, transfer_id, expected_seq);
        continue;
      }
      if (frame.sequence != expected_seq || stored) {
        send_control(api, K6_CA_NAK, transfer_id, expected_seq);
        continue;
      }
      {
        if (received >= length) {
          send_control(api, K6_CA_NAK, transfer_id, expected_seq);
          continue;
        }
        size_t count = length - received;
        unsigned i;
        if (count > K6_CA_PAYLOAD_BYTES) count = K6_CA_PAYLOAD_BYTES;
        for (i = (unsigned)count; i < K6_CA_PAYLOAD_BYTES; ++i)
          if (frame.payload[i]) break;
        if (i != K6_CA_PAYLOAD_BYTES || !count) {
          send_control(api, K6_CA_NAK, transfer_id, expected_seq);
          continue;
        }
        memcpy(scratch + received, frame.payload, count);
        received += count; ++expected_seq;
      }
      send_control(api, K6_CA_ACK, transfer_id, expected_seq);
      p = ui_put(line, "RX "); p = ui_dec(p, (uint32_t)received, 4);
      *p++ = '/'; p = ui_dec(p, (uint32_t)length, 4); *p = 0;
      ui_text(ui, 2, line); ui_flush(ui, 250);
      continue;
    }
    if (frame.kind == K6_CA_END && frame.sequence == expected_seq && received == length &&
        parse_meta(frame.payload, &meta_len, &meta_crc) && meta_len == length &&
        meta_crc == expected_crc && pxk6_crc32(scratch, length) == expected_crc &&
        pxk6_card_open(scratch, length, &card) == 0) {
      int slot = api->card_store(scratch, (uint32_t)length);
      if (slot < 0) {
        send_control(api, K6_CA_NAK, transfer_id, expected_seq);
        ui_text(ui, 2, "STORE FAILED"); ui_now(ui);
        continue;
      }
      stored = 1; success_at = api->ms(); ++expected_seq;
      send_control(api, K6_CA_ACK, transfer_id, expected_seq);
      show_preview(api, ui, &card, preview);
      continue;
    }
    if (stored && frame.kind == K6_CA_END && frame.sequence + 1 == expected_seq) {
      send_control(api, K6_CA_ACK, transfer_id, expected_seq);
      show_preview(api, ui, &card, preview);
      success_at = api->ms();
      continue;
    }
    if (frame.sequence >= expected_seq) send_control(api, K6_CA_NAK, transfer_id, expected_seq);
    if ((uint32_t)(api->ms() - last_frame) > 300000u) return -1;
  }
}

int k6_main(const k6_api_t *api) {
  ui_t ui;
  uint32_t scratch_bytes = 0;
  uint8_t *scratch;
  int key, result;
  if (api->level < 24 ||
      api->card_store == 0 || api->scratch == 0) {
    api->say("CARD LINK needs API 24");
    return -1;
  }
  scratch = (uint8_t *)api->scratch(&scratch_bytes);
  if (!scratch || scratch_bytes < PXK6_MAX_RECORD_BYTES) {
    api->say("CARD LINK needs 2800 B RAM");
    return -2;
  }
  ui_begin(&ui, api, "CARD LINK", "1 BROWSE  2 RECEIVE");
  ui_release(&ui);
  show_frequency(&ui, api, "VHF");
  ui_text(&ui, 5, "1:BROWSE 2:RX X:EXIT"); ui_now(&ui);
  for (;;) {
    key = ui_key(&ui);
    if (key == 'X' || (!ui.on && api->key_down())) return 0;
    if (key == '1') { result = run_sender(api, &ui, scratch, scratch_bytes); break; }
    if (key == '2') { result = run_receiver(api, &ui, scratch, scratch_bytes); break; }
    api->delay_us(10000);
  }
  api->fsk_rx_stop();
  api->tx_off();
  if (result == ERR_CANCEL || result == ERR_STOP) ui_end(&ui, "Stopped", 1);
  else if (result == ERR_NO_CARD) ui_end(&ui, "No valid card", 0);
  else if (result == ERR_TX) ui_end(&ui, "Transfer failed", 0);
  else if (result == ERR_RAM) ui_end(&ui, "Need more RAM", 0);
  else if (result == ERR_UI) ui_end(&ui, "Use radio screen", 0);
  else ui_end(&ui, result < 0 ? "Transfer failed" : "Done", 0);
  return result;
}
