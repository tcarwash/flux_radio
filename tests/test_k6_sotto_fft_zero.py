"""Exact-source safety guard for zero-imaginary FFT bit-reversal optimization."""
from tools.k6_sotto_module import adapt,FIXED

def test_sotto_fft_zero_imaginary_swap_removed_only_in_generated_code():
    source=(FIXED/'sotto_fx_slim.c').read_text()
    original='t = im[i], im[i] = im[j], im[j] = t;'
    assert original in source
    transformed=adapt(source)
    assert original not in transformed
    assert transformed.count('skip swapping two known-zero imaginary samples') == 1
    assert transformed.count('    im[i] = 0;') == 1
    assert transformed.count('  fft256(re, im);') == 1
    assert 're[i] = re[j], re[j] = t;' in transformed
