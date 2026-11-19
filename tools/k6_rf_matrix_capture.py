#!/usr/bin/env python3
# Experimental live RF test, one TX per invocation, no retry. See docs/k6-extra-low-power-lab.md.
import ctypes as C, numpy as np, threading, time, sys, serial, struct, json
from pathlib import Path
root=Path("serial-test-output/tx-matrix");root.mkdir(parents=True,exist_ok=True)
target,baud,nbytes=map(int,sys.argv[1:4]); assert target<=180 and nbytes%2==0 and 2<=nbytes<=128
word=round(0x3065*baud/1200);assert word<=65535
stem=root/f"apc{target}-baud{baud}-len{nbytes}"
if stem.with_suffix('.npy').exists() or stem.with_suffix('.json').exists():
 raise SystemExit('Refusing to overwrite existing capture; move it or choose another combination')
import k6_install as k
l=C.CDLL('librtlsdr.so.0'); p=C.c_void_p()
for n,args in {'open':[C.POINTER(C.c_void_p),C.c_uint32],'set_center_freq':[C.c_void_p,C.c_uint32],'set_sample_rate':[C.c_void_p,C.c_uint32],'set_tuner_gain_mode':[C.c_void_p,C.c_int],'set_tuner_gain':[C.c_void_p,C.c_int],'reset_buffer':[C.c_void_p],'read_sync':[C.c_void_p,C.c_void_p,C.c_int,C.POINTER(C.c_int)],'close':[C.c_void_p]}.items():getattr(l,'rtlsdr_'+n).argtypes=args
assert l.rtlsdr_open(C.byref(p),0)==0
for fn,v in [('set_center_freq',433750000),('set_sample_rate',1024000),('set_tuner_gain_mode',1),('set_tuner_gain',0)]:assert getattr(l,'rtlsdr_'+fn)(p,v)==0
assert l.rtlsdr_reset_buffer(p)==0
chunks=[]; ready=threading.Event(); done=threading.Event(); errors=[]
def capture():
 try:
  b=C.create_string_buffer(262144); got=C.c_int()
  for i in range(64):
   assert l.rtlsdr_read_sync(p,b,len(b),C.byref(got))==0
   chunks.append(np.frombuffer(b.raw[:got.value],dtype=np.uint8).copy())
   if i==7: ready.set()
 except Exception as e:errors.append(e)
 finally:done.set()
try:
 with serial.Serial('/dev/ttyUSB0',115200,timeout=.005) as port:
  c=k.open_cable(port,print,8); assert c
  c.timeout=12
  t=threading.Thread(target=capture);t.start()
  try:
   assert ready.wait(5) and not done.is_set() and not errors
   raw=np.concatenate(chunks[:8]); z=(raw[::2].astype('f')-127.5)+1j*(raw[1::2].astype('f')-127.5); n=8192; a=z[:len(z)//n*n].reshape(-1,n); spec=np.mean(abs(np.fft.fftshift(np.fft.fft(a*np.hanning(n),axis=1),axes=1))**2,axis=0); f=np.fft.fftshift(np.fft.fftfreq(n,1/1024000)); peak=10*np.log10(spec[abs(f+275000)<55000].max()/np.median(spec)); print('Baseline channel peak dB above median:',peak,flush=True)
   if peak>18:raise RuntimeError('Channel appears occupied; not sending')
   print('SDR recording; sending exactly one request at '+sys.argv[1]+' APC target',flush=True)
   result=struct.unpack('<7i',c.calls([(0x79,0,struct.pack('<IBHB',433500000,target,word,nbytes//2))],retries=0)[0]); print('Radio result:',result,flush=True); stem.with_suffix('.json').write_text(json.dumps(dict(target=target,baud=baud,baudword=word,nbytes=nbytes,result=result),indent=2)); assert result[0]>=0
  finally:
   c.end();t.join();np.save(stem.with_suffix('.npy'),np.concatenate(chunks)); print('Capture saved; chunks:',len(chunks),'errors:',errors,flush=True)
finally:l.rtlsdr_close(p)
