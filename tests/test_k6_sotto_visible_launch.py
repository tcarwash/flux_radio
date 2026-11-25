"""Sotto's physical PTT waiting screen must be visible before any TX call."""
from pathlib import Path

def test_live_module_flushes_launch_screen_before_ptt_wait():
    code=(Path(__file__).resolve().parents[1]/"c/k6/mod/sotto_live_tx.c").read_text()
    start=code.index('a->lcd_text(0,"SOTTO TX BENCH");')
    lines=code.index('a->lcd_text(2,"Hold PTT to speak");',start)
    flush=code.index('(void)a->lcd_flush();',lines)
    ptt=code.index('r=a->mic_stream(3,0,0);',flush)
    tx=code.index('if(a->tx_on(25000u))',flush)
    assert start < lines < flush < ptt < tx
    assert code.count('a->lcd_flush()')==1
