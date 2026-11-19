from collections import deque
from sim.k6ide import K6Workbench

class Board:
    cycles=0
    key_scan_epoch=0
    key_sample_epoch=0
    def __init__(self): self.events=[]
    def press_key(self,key,down): self.events.append((self.cycles,key,down))

def test_fast_clicks_are_held_and_released_in_virtual_time():
    w=K6Workbench.__new__(K6Workbench)
    w.board=Board();w.input_queue=deque([('MENU',True),('MENU',False),('UP',True),('UP',False)])
    w.active_key=None;w.next_key_at=0;w.release_scan_start=0
    for t in (0,1,9_599_999,9_600_000,17_279_999,17_280_000,17_280_001,26_880_000):
        w.board.cycles=t;w._service_keys()
    assert w.board.events == [(0,'MENU',True),(9_600_000,'MENU',False),(17_280_000,'UP',True),(26_880_000,'UP',False)]


def test_key_can_release_early_when_two_scans_observed():
    w=K6Workbench.__new__(K6Workbench)
    w.board=Board();w.input_queue=deque([('MENU',True),('MENU',False),('UP',True)])
    w.active_key=None;w.next_key_at=0;w.release_scan_start=0
    w._service_keys();w.board.key_sample_epoch=1;w._service_keys()
    assert w.active_key=='MENU'
    w.board.key_sample_epoch=2;w._service_keys();assert w.active_key is None
    w.board.key_scan_epoch=3;w._service_keys();assert w.active_key=='UP'
