#!/usr/bin/env python3
"""Share channel bitmap traversal, including +NEW wrap and empty-slot search."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/app/chanmgr.rs';s=p.read_text()
if 'fn find_slot(' not in s:
 a=s.index('    fn step(&self, down: bool)');b=s.index('\n}\n\nfn rescan_list',a)
 s=s[:a]+'''    #[inline(never)]
    fn find_slot(occupied:&[u8;OCCUPIED_BYTES],mut n:i32,delta:i32,mut skip:usize,want:bool)->Option<u16>{
        while n>=0 && n<=i32::from(MAX_CHANNEL_NUM){
            if Self::bit(occupied,n as u16)==want {
                if skip==0{return Some(n as u16);}
                skip-=1;
            }
            n+=delta;
        }
        None
    }
    fn step(&self,down:bool)->Option<u16>{
        let (start,delta)=if down{(self.selected.map_or(0,|n|i32::from(n)+1),1)}else{(self.selected.map_or(i32::from(MAX_CHANNEL_NUM),|n|i32::from(n)-1),-1)};
        Self::find_slot(&self.occupied,start,delta,0,true)
    }
    fn selected_index(&self)->usize{
        let end=self.selected.map_or(MAX_CHANNEL_NUM+1,|n|n);
        let mut count=0;let mut n=0;
        while n<end{if Self::bit(&self.occupied,n){count+=1;}n+=1;}
        count
    }
    fn channel_at(&self,index:usize)->Option<u16>{Self::find_slot(&self.occupied,0,1,index,true)}
'''+s[b:]
 s=s.replace('(0..=MAX_CHANNEL_NUM).find(|&n| Self::bit(&occupied, n))','Self::find_slot(&occupied,0,1,0,true)').replace('(0..=MAX_CHANNEL_NUM).find(|&n| !ChanList::bit(&list.occupied, n))','ChanList::find_slot(&list.occupied,0,1,0,false)')
 p.write_text(s)
