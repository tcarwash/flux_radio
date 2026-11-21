/* txpathid: READ-ONLY FD6818B RF/modulator-path fingerprint.
   Single API-level-2 RAM execution, no TX, no register/GPIO/SPI writes.
   Registers are sampled solely for comparing the B-chip to the older
   FD6818 0x4819 programming-guide path. Never assume same semantics.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"

static const unsigned char regs[] = {
  0x00, 0x2b, 0x30, 0x38, 0x39, 0x3a, 0x3b, 0x3c,
  0x40, 0x43, 0x47, 0x48, 0x50, 0x58, 0x59,
  0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x7d, 0x7e
};

static const char digits[] = "0123456789abcdef";
static char *h2(char *p, unsigned x) {
  *p++ = digits[(x >> 4) & 15u];
  *p++ = digits[x & 15u];
  return p;
}
static char *h4(char *p, unsigned x) {
  p = h2(p, x >> 8);
  return h2(p, x);
}
int k6_main(const k6_api_t *api) {
  char line[60], *p;
  unsigned i;
  if (!api || api->abi != 1 || api->level < 2) return -3;
  if (api->fd_read(0x00) != 0x4829u) {
    api->say("txpathid skip: not FD6818B");
    return -6;
  }
  if (api->key_down()) {
    api->say("txpathid skip: key pressed");
    return -2;
  }
  api->say("txpathid begin RX read-only");
  for (i = 0; i < sizeof(regs); i += 3) {
    unsigned j;
    p = line;
    for (j = 0; j < 3 && i + j < sizeof(regs); j++) {
      unsigned v, reg = regs[i + j];
      if (j) *p++ = ' ';
      *p++ = 'r';
      p = h2(p, reg);
      *p++ = '=';
      v = api->fd_read((unsigned char)reg);
      p = h4(p, v);
    }
    *p = 0;
    api->say(line);
  }
  api->say("txpathid end no writes");
  return (int)sizeof(regs);
}
