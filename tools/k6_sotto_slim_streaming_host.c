/* Offline host proof of the *actual* incremental slim Sotto 1200 C API.
 * Reads 80 samples (10ms @ 8kHz) at a time; writes one 6B output when
 * slim_encode10() reports a completed superframe. Calls slim_flush() only
 * at end of file. No K6 UART, module API, hardware or RF. This is NOT
 * actual real-time scheduling or MCU-cycle verification.
 * Build with -I<path>/sotto/c/fixed and the upstream slim source+tables.
 * SPDX-License-Identifier: MIT
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "sotto_slim.h"
#ifdef SLIM_TUNE
extern long slim_stat[4]; /* optional host-only instrumentation of codebook reads */
#endif

static void emit(FILE *out,FILE *timeline,const uint8_t buf[6],
                 unsigned nblocks,unsigned frame_no,int flushing) {
  if(fwrite(buf,1,6,out)!=6){perror("write bits");exit(3);}
  if(timeline && fprintf(timeline,"%u,%u,%d\n",frame_no,nblocks,flushing)<0){perror("timeline");exit(3);}
}
int main(int argc,char **argv){
  FILE *src,*dst,*timeline,*stats=NULL;
  slim_enc_t *enc;
  unsigned blocks=0,frames=0;
  long prev_reads=0;
  int16_t chunk[80];
  uint8_t encoded[6];
  if(argc!=4 && argc!=5){fprintf(stderr,"usage: k6_sotto_slim_streaming_host INPUT_8K_S16.raw OUTPUT.bit TIMELINE.csv [CODEBOOK_STATS.csv]\n");return 2;}
  src=fopen(argv[1],"rb");dst=fopen(argv[2],"wb");timeline=fopen(argv[3],"w");
  if(argc==5) {
#ifdef SLIM_TUNE
    stats=fopen(argv[4],"w");
#else
    fprintf(stderr,"stats require host -DSLIM_TUNE build\n");return 2;
#endif
  }
  if(!src||!dst||!timeline||(argc==5&&!stats)){perror("open stream");return 2;}
  enc=calloc(1,slim_enc_bytes());
  if(!enc){perror("allocate encoder state");return 2;}
  slim_enc_init(enc);
  fputs("superframe_index,10ms_blocks_received,flush_phase\n",timeline);
  if(stats) fputs("10ms_block_index,codebook_vectors_read_this_call,codebook_vectors_read_cumulative\n",stats);
  for(;;){
    size_t n=fread(chunk,sizeof(*chunk),80,src);
    if(n==0)break;
    if(n!=80){fprintf(stderr,"Input must end on a 10-ms/80-sample boundary\n");return 2;}
    blocks++;
    if(slim_encode10(enc,chunk,encoded))emit(dst,timeline,encoded,blocks,frames++,0);
#ifdef SLIM_TUNE
    if(stats){
      long now=slim_stat[2];
      if(fprintf(stats,"%u,%ld,%ld\n",blocks-1,now-prev_reads,now)<0)return 3;
      prev_reads=now;
    }
#endif
  }
  while(slim_flush(enc,encoded))emit(dst,timeline,encoded,blocks,frames++,1);
  if(fclose(src)||fclose(dst)||fclose(timeline)||(stats&&fclose(stats)))return 3;
  free(enc);
  fprintf(stderr,"incremental slim encoded %u 10-ms blocks to %u Sotto1200 superframes\n",blocks,frames);
  return 0;
}
