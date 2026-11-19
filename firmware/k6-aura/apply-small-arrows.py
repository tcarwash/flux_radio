#!/usr/bin/env python3
"""Avoid pulling in general triangle rasterization for two 7x5 menu arrows."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/ui/list.rs'
s=p.read_text()
if '// Flux small arrows' not in s:
    s=s.replace('PrimitiveStyle, Rectangle, Triangle','PrimitiveStyle, Rectangle')
    for name,dy,half in [('up','row - 2','(row * 3 + 1) / 4'),('down','2 - row','(row * 3 + 2) / 4')]:
        a=s.index('    Triangle::new(',s.index('fn draw_triangle_'+name)); b=s.index('    .ok();',a)+len('    .ok();')
        s=s[:a]+f'''    // Flux small arrows
    for row in 0..5i32 {{
        let half = {half};
        Rectangle::new(Point::new(cx-half,cy+({dy})), Size::new((half*2+1) as u32,1))
            .into_styled(PrimitiveStyle::with_fill(fg)).draw(lcd).ok();
    }}'''+s[b:]
    p.write_text(s)
